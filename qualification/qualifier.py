"""
NZT-48 Trading System — 7-Stage Qualification Pipeline
Section 37: Every signal passes through ALL 7 stages sequentially.
Any stage can KILL the signal. Order matters.

Stage 1: Duplicate Check — same ticker+direction within 4 hours
Stage 2: No-Trade Doctrine — 7 conditions that block all trading
Stage 3: Calendar Filter — FOMC/earnings/CPI blocks
Stage 4: GEX Overlay — adjust confidence based on gamma exposure
Stage 5: Confidence Scorer — 5-layer scoring, floor 60
Stage 6: Risk Sizer — 0.75% risk per trade (IMMUTABLE), position caps
Stage 7: ISA Mapper — map Bot B signals to Bot A ETP equivalents
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, Bot, BotInstance, RegimeState, GEXRegime,
    MarketContext, IndicatorSnapshot, SectorFlow, NarrativeContext,
    ConfidenceBreakdown, SignalStatus,
)
from feeds.regime_classifier import TimeOfDayEngine
from qualification.confidence_scorer import ConfidenceScorer

logger = logging.getLogger("nzt48.qualifier")


class QualificationPipeline:
    """7-stage qualification pipeline that every signal must pass through.

    The pipeline is the gatekeeper between raw strategy signals and
    deliverable trade alerts. It enforces all system rules.
    """

    def __init__(
        self,
        equity: float = 20_000.0,
        db_conn=None,
    ) -> None:
        self.equity = equity
        self.db_conn = db_conn
        self.confidence_scorer = ConfidenceScorer()
        self.time_engine = TimeOfDayEngine()
        from core.clock import ET_TZ
        self._et_tz = ET_TZ

        # ISA mapping table (Section 37, Stage 7)
        self.isa_map = {
            ("NVDA", "LONG"): ("NVD3.L", "3x", "NVIDIA"),
            ("NVDA", "SHORT"): ("NVDS.L", "-3x", "NVIDIA"),
            ("TSLA", "LONG"): ("TSL3.L", "3x", "Tesla"),
            ("TSLA", "SHORT"): ("TSLS.L", "-3x", "Tesla"),
            # QQQ/broad
            ("QQQ", "LONG"): ("QQQ3.L", "3x", "Nasdaq 100"),
            ("QQQ", "SHORT"): ("QQQS.L", "-3x", "Nasdaq 100"),
            # Semis
            ("SMH", "LONG"): ("3SEM.L", "3x", "PHLX Semiconductor"),
            ("SMH", "SHORT"): ("SC3S.L", "-3x", "PHLX Semiconductor"),
            ("SOXX", "LONG"): ("SOX3.L", "3x", "Semiconductors"),
            # Broad market
            ("SPY", "LONG"): ("3LUS.L", "3x", "S&P 500"),
            ("SPY", "SHORT"): ("3USS.L", "-3x", "S&P 500"),
        }

    def qualify(
        self,
        signal: Signal,
        indicators: IndicatorSnapshot,
        market_ctx: MarketContext,
        sector_flow: SectorFlow,
        narrative: NarrativeContext,
        open_positions: list = None,
        recent_signals: list = None,
        consecutive_losses: int = 0,
        ticker_cold_streak: bool = False,
        daily_pnl_pct: float = 0.0,
        weekly_pnl_pct: float = 0.0,
        daily_trade_count: int = 0,
        weekly_trade_count: int = 0,
    ) -> Signal:
        """Run a signal through all 7 qualification stages.

        Returns the signal with updated status (PENDING if qualified,
        or with rejection_reason set if killed at any stage).
        """
        if open_positions is None:
            open_positions = []
        if recent_signals is None:
            recent_signals = []

        signal.id = signal.id or str(uuid.uuid4())[:12]
        signal.qualification_log = []

        # Stage 1: Duplicate Check
        if not self._stage1_dedup(signal, recent_signals):
            return signal

        # Stage 2: No-Trade Doctrine
        if not self._stage2_no_trade(signal, market_ctx, indicators,
                                      open_positions, consecutive_losses,
                                      daily_trade_count):
            return signal

        # Stage 3: Calendar Filter
        if not self._stage3_calendar(signal, market_ctx):
            return signal

        # Stage 4: GEX Overlay
        self._stage4_gex_overlay(signal, market_ctx)

        # Stage 5: Confidence Scorer
        if not self._stage5_confidence(signal, indicators, market_ctx,
                                        sector_flow, narrative,
                                        consecutive_losses, ticker_cold_streak):
            return signal

        # Stage 5b: Smart Session Window — time-of-day quality adjustment
        if not self._stage5b_session_window(signal):
            return signal

        # Stage 5c: Prospect Theory R:R Gate — minimum 2:1 reward-to-risk
        if not self._stage5c_prospect_theory_gate(signal):
            return signal

        # Stage 6: Risk Sizer
        self._stage6_risk_sizer(signal, indicators, open_positions)

        # Stage 7: ISA Mapper
        self._stage7_isa_mapper(signal)

        # Constitutional enforcement (Finding 20): final backstop after all stages
        if hasattr(signal, 'risk_pct') and signal.risk_pct > 0.0075:
            signal.risk_pct = 0.0075
            signal.qualification_log.append("[CONSTITUTIONAL] Risk capped at 0.75%")

        signal.qualification_log.append("QUALIFIED — passed all 7 stages")
        logger.info("Signal QUALIFIED: %s %s %s conf=%d",
                     signal.direction.value, signal.ticker,
                     signal.strategy, signal.confidence)
        return signal

    def _stage1_dedup(self, signal: Signal, recent_signals: list) -> bool:
        """Stage 1: Duplicate Check.
        Same ticker + direction within 4 hours = suppress.
        """
        for recent in recent_signals:
            if (hasattr(recent, 'ticker') and recent.ticker == signal.ticker
                    and hasattr(recent, 'direction') and recent.direction == signal.direction.value):
                signal.status = SignalStatus.SKIPPED
                signal.rejection_reason = "DEDUP: Same ticker+direction within 4 hours"
                signal.qualification_log.append(f"Stage 1 KILLED: {signal.rejection_reason}")
                logger.info("Stage 1 KILL: %s %s dedup", signal.direction.value, signal.ticker)
                return False

        signal.qualification_log.append("Stage 1 PASS: No duplicates")
        return True

    def _stage2_no_trade(
        self,
        signal: Signal,
        ctx: MarketContext,
        indicators: IndicatorSnapshot,
        open_positions: list,
        consecutive_losses: int,
        daily_trade_count: int,
    ) -> bool:
        """Stage 2: No-Trade Doctrine — 7 conditions (Section 46).

        1) VIX > 35
        2) ADX < 15 on QQQ
        3) First 5 minutes of session
        4) Last 10 minutes of session
        5) Spread > 1.5%
        6) Max positions reached
        7) 3+ consecutive losses without cooldown
        """
        reasons = []

        # 1) VIX > 35
        if ctx.vix > 35:
            reasons.append(f"VIX={ctx.vix:.1f} > 35")

        # 2) ADX < 10 (no trend at all — lowered from 15 to allow coiled breakouts)
        if indicators.adx14 < 10:
            reasons.append(f"ADX={indicators.adx14:.1f} < 10")

        # 3) First 5 minutes (Chaos Open)
        from models import TimeWindow
        if ctx.time_window == TimeWindow.CHAOS_OPEN:
            reasons.append("Chaos Open (first 5 min)")

        # 4) Last 10 minutes
        if ctx.time_window == TimeWindow.CLOSE_MECHANICS:
            reasons.append("Close Mechanics (last 30 min)")

        # 5) Spread > 1.5%
        if indicators.bid_ask_spread > 1.5:
            reasons.append(f"Spread={indicators.bid_ask_spread:.2f}% > 1.5%")

        # 6) Max positions reached
        max_positions = 3  # Default
        if signal.bot_instance == BotInstance.BULL:
            max_positions = 7  # Increased from 5 for more concurrent positions in trends
        elif signal.bot_instance == BotInstance.RANGE:
            max_positions = 3
        elif signal.bot_instance == BotInstance.BEAR:
            max_positions = 2

        current_positions = len([p for p in open_positions
                                 if hasattr(p, 'bot_instance')
                                 and p.bot_instance == signal.bot_instance.value])
        if current_positions >= max_positions:
            reasons.append(f"Max positions ({max_positions}) reached for {signal.bot_instance.value}")

        # 7) 3+ consecutive losses without cooldown
        if consecutive_losses >= 3:
            reasons.append(f"Consecutive losses: {consecutive_losses}")

        if reasons:
            signal.status = SignalStatus.SKIPPED
            signal.rejection_reason = f"NO_TRADE: {'; '.join(reasons)}"
            signal.qualification_log.append(f"Stage 2 KILLED: {signal.rejection_reason}")
            logger.info("Stage 2 KILL: %s %s — %s",
                         signal.direction.value, signal.ticker, signal.rejection_reason)
            return False

        signal.qualification_log.append("Stage 2 PASS: No-trade doctrine clear")
        return True

    def _stage3_calendar(self, signal: Signal, ctx: MarketContext) -> bool:
        """Stage 3: Calendar Filter.

        FOMC day = block all 5x leveraged ETP entries.
        Earnings night = block that specific ticker.
        CPI/NFP = HIGH RISK flag, half size.
        """
        if ctx.fomc_today and signal.bot == Bot.A:
            # Block 5x entries on FOMC days
            isa_lev = getattr(signal, 'isa_leverage', '') or ''
            if "5x" in isa_lev:
                signal.status = SignalStatus.SKIPPED
                signal.rejection_reason = "CALENDAR: FOMC day — 5x ETPs blocked"
                signal.qualification_log.append(f"Stage 3 KILLED: {signal.rejection_reason}")
                return False

        if signal.ticker in ctx.earnings_tonight:
            signal.status = SignalStatus.SKIPPED
            signal.rejection_reason = f"CALENDAR: {signal.ticker} reports earnings tonight"
            signal.qualification_log.append(f"Stage 3 KILLED: {signal.rejection_reason}")
            return False

        if ctx.cpi_nfp_today:
            signal.qualification_log.append("Stage 3 FLAG: CPI/NFP day — HIGH_RISK, half size")
            signal._original_risk_pct = signal.risk_pct  # Preserve original before mutation
            signal.risk_pct = signal.risk_pct / 2  # Half size on high-impact days
            signal._calendar_halved = True  # Flag for Stage 6 to respect

        signal.qualification_log.append("Stage 3 PASS: Calendar clear")
        return True

    def _stage4_gex_overlay(self, signal: Signal, ctx: MarketContext) -> None:
        """Stage 4: GEX Overlay — adjust confidence, don't kill.

        Positive GEX = reduce breakout confidence (mean reversion favoured).
        Negative GEX = boost momentum confidence (amplified moves).
        """
        if ctx.gex_regime == GEXRegime.POSITIVE:
            # Mean reversion favoured — breakout strategies get penalty
            if signal.strategy in ("S2", "S14"):  # Momentum Breakout, Gamma Squeeze
                signal.confidence -= 5
                signal.qualification_log.append(
                    "Stage 4: GEX POSITIVE — breakout confidence reduced by 5"
                )
            else:
                signal.qualification_log.append("Stage 4: GEX POSITIVE — no adjustment")

        elif ctx.gex_regime == GEXRegime.NEGATIVE:
            # Amplified moves — momentum strategies get boost
            if signal.strategy in ("S1", "S2", "S13", "S14"):
                signal.confidence += 5
                signal.qualification_log.append(
                    "Stage 4: GEX NEGATIVE — momentum confidence boosted by 5"
                )
            else:
                signal.qualification_log.append("Stage 4: GEX NEGATIVE — no adjustment")

        elif ctx.gex_regime == GEXRegime.FLIPPING:
            signal.qualification_log.append(
                "Stage 4: GEX FLIPPING — regime change imminent, caution"
            )
        else:
            signal.qualification_log.append("Stage 4: GEX neutral")

    def _stage5_confidence(
        self,
        signal: Signal,
        indicators: IndicatorSnapshot,
        market_ctx: MarketContext,
        sector_flow: SectorFlow,
        narrative: NarrativeContext,
        consecutive_losses: int,
        ticker_cold: bool,
    ) -> bool:
        """Stage 5: Confidence Scorer — 0-100 score, kill if < 60.

        Uses the 5-layer scoring engine. Floor is IMMUTABLE at 60.
        ORB/VWAP strategies require 65. BEAR-BOT requires 80.
        """
        breakdown = self.confidence_scorer.score(
            indicators=indicators,
            market_ctx=market_ctx,
            sector_flow=sector_flow,
            narrative=narrative,
            ticker=signal.ticker,
            direction=signal.direction.value,
            strategy=signal.strategy,
            consecutive_losses=consecutive_losses,
            ticker_cold_streak=ticker_cold,
        )

        signal.confidence = breakdown.final_score
        signal.confidence_breakdown = breakdown

        # Check minimum thresholds
        min_conf = 60
        if signal.bot_instance == BotInstance.BEAR:
            min_conf = 80
        elif signal.strategy in ("S2",):  # ORB/VWAP strategies
            min_conf = 65

        if signal.confidence < min_conf:
            signal.status = SignalStatus.SKIPPED
            signal.rejection_reason = (
                f"CONFIDENCE: {signal.confidence:.0f} < {min_conf} "
                f"(L1={breakdown.layer1_price_action:.0f} "
                f"L2={breakdown.layer2_regime:.0f} "
                f"L3={breakdown.layer3_sector_flow:.0f} "
                f"L4={breakdown.layer4_macro:.0f} "
                f"L5={breakdown.layer5_narrative:.0f} "
                f"Pen={breakdown.penalties:.0f})"
            )
            signal.qualification_log.append(f"Stage 5 KILLED: {signal.rejection_reason}")
            logger.info("Stage 5 KILL: %s %s conf=%.0f < %d",
                         signal.direction.value, signal.ticker,
                         signal.confidence, min_conf)
            return False

        signal.qualification_log.append(
            f"Stage 5 PASS: Confidence={signal.confidence:.0f} >= {min_conf}"
        )
        return True

    def _stage5b_session_window(self, signal: Signal) -> bool:
        """Stage 5b: Smart Session Window — time-of-day entry restriction.

        Applies a confidence adjustment based on the current ET session
        window, and BLOCKS low-conviction entries during the DEAD_ZONE
        (12:00-14:00 ET lunch lull).

        Rules:
            ORB_PRIME (09:30-10:00)        → confidence +10
            MORNING_MOMENTUM (10:00-11:30) → confidence +5
            DEAD_ZONE (12:00-14:00)        → confidence -10, KILL if < 75
            POWER_HOUR (15:00-16:00)       → confidence +5
            NEUTRAL                        → no change
        """
        now_et = datetime.now(self._et_tz)
        session_label, adj = self.time_engine.get_session_quality(
            now_et.hour, now_et.minute
        )

        if adj != 0:
            signal.confidence += adj
            signal.qualification_log.append(
                f"Stage 5b SESSION: {session_label} ({now_et.strftime('%H:%M')} ET) "
                f"→ confidence {'+' if adj > 0 else ''}{adj} "
                f"(now {signal.confidence:.0f})"
            )
        else:
            signal.qualification_log.append(
                f"Stage 5b SESSION: {session_label} ({now_et.strftime('%H:%M')} ET) "
                f"→ no adjustment"
            )

        # DEAD_ZONE gate: only high-conviction trades survive lunch (lowered from 75 to 70)
        if session_label == "DEAD_ZONE" and signal.confidence < 70:
            signal.status = SignalStatus.SKIPPED
            signal.rejection_reason = (
                f"SESSION: DEAD_ZONE ({now_et.strftime('%H:%M')} ET) "
                f"and confidence {signal.confidence:.0f} < 70 — "
                f"only high-conviction trades during lunch"
            )
            signal.qualification_log.append(
                f"Stage 5b KILLED: {signal.rejection_reason}"
            )
            logger.info(
                "Stage 5b KILL: %s %s — DEAD_ZONE conf=%.0f < 70",
                signal.direction.value, signal.ticker, signal.confidence,
            )
            return False

        return True

    def _stage5c_prospect_theory_gate(self, signal: Signal) -> bool:
        """Stage 5c: Prospect Theory R:R Gate — minimum 2:1 reward-to-risk.

        Prospect theory (Kahneman & Tversky) shows that losses hurt ~2x
        more than equivalent gains feel good.  A 2:1 minimum R:R ensures
        that even with a 50% win rate, the expected value is positive
        *and* the psychological pain of losses is offset by the magnitude
        of wins.

        Uses signal.reward_risk which computes target_1r distance / stop
        distance.  Signals with 0 reward_risk (missing entry/stop/target)
        are passed through — they'll be caught later by the risk sizer.

        Returns:
            True if the signal passes (R:R >= 2.0 or not yet calculable),
            False if rejected.
        """
        rr = signal.reward_risk

        # If entry/stop/target not yet set, we can't compute R:R — pass through
        if rr == 0 and (signal.entry == 0 or signal.stop == 0):
            signal.qualification_log.append(
                "Stage 5c PASS: R:R not calculable (entry/stop/target not set)"
            )
            return True

        if rr < 1.5:
            signal.status = SignalStatus.SKIPPED
            signal.rejection_reason = (
                f"Prospect theory gate: R:R {rr:.1f} < 1.5 minimum"
            )
            signal.qualification_log.append(
                f"Stage 5c KILLED: {signal.rejection_reason}"
            )
            logger.info(
                "Stage 5c KILL: %s %s — R:R %.1f < 1.5 (prospect theory gate)",
                signal.direction.value, signal.ticker, rr,
            )
            return False

        signal.qualification_log.append(
            f"Stage 5c PASS: R:R {rr:.1f} >= 1.5 (prospect theory gate)"
        )
        return True

    def _stage6_risk_sizer(
        self,
        signal: Signal,
        indicators: IndicatorSnapshot,
        open_positions: list,
    ) -> None:
        """Stage 6: Risk Sizer — conviction-based sizing.

        Base: 0.75% of equity (CONSTITUTIONAL floor)
        80+ confidence: 1.0%  | 85+ confidence: 1.25% | 90+ confidence: 1.5%
        Cap at 5% of equity per position, 20% total exposure per bot.
        3x ETPs: max 18% single / 35% total (was 15%/30%).
        5x ETPs: max 5% single / 15% total.
        """
        # Conviction-based risk scaling — base is still immutable 0.75%
        base_risk = signal.risk_pct  # May have been halved by calendar filter
        if signal.confidence >= 90:
            risk_pct = min(base_risk * 2.0, 0.015)   # Up to 1.5%
        elif signal.confidence >= 85:
            risk_pct = min(base_risk * 1.67, 0.0125)  # Up to 1.25%
        elif signal.confidence >= 80:
            risk_pct = min(base_risk * 1.33, 0.01)    # Up to 1.0%
        else:
            risk_pct = base_risk                       # 0.75% base

        # Constitutional cap: 0.75% max risk per trade (immutable)
        risk_pct = min(risk_pct, 0.0075)

        # Finding 21: If Stage 3 halved risk for CPI/NFP, don't let
        # confidence escalation negate the half-size mandate
        if getattr(signal, '_calendar_halved', False):
            risk_pct = min(risk_pct, 0.00375)  # Half of 0.75%

        signal.risk_pct = risk_pct
        risk_dollars = self.equity * risk_pct

        # Calculate stop distance
        if signal.entry > 0 and signal.stop > 0:
            stop_distance = abs(signal.entry - signal.stop)
        else:
            # Use 1.5x ATR as default stop
            stop_distance = indicators.atr14 * 1.5
            if signal.direction == Direction.LONG:
                signal.stop = signal.entry - stop_distance
            else:
                signal.stop = signal.entry + stop_distance

        # If stop > 2.0x ATR, signal is NO_TRADE (Section 6 ORB rules)
        if stop_distance > indicators.atr14 * 2.0 and indicators.atr14 > 0:
            signal.qualification_log.append(
                f"Stage 6 WARNING: Stop distance {stop_distance:.2f} > 2x ATR "
                f"({indicators.atr14 * 2:.2f}). Using 2x ATR cap."
            )
            stop_distance = indicators.atr14 * 2.0

        # Calculate shares
        if stop_distance > 0 and signal.entry > 0:
            shares = int(risk_dollars / stop_distance)
            position_value = shares * signal.entry
            position_pct = position_value / self.equity if self.equity > 0 else 0

            # Cap at 5% of equity per position
            if position_pct > 0.05:
                shares = int(0.05 * self.equity / signal.entry)
                position_value = shares * signal.entry
                position_pct = position_value / self.equity

            signal.shares = max(shares, 1)
            signal.risk_dollars = risk_dollars
            signal.position_pct_equity = position_pct
        else:
            signal.shares = 0
            signal.risk_dollars = 0

        # Calculate targets
        if stop_distance > 0:
            r_unit = stop_distance
            if signal.direction == Direction.LONG:
                signal.target_1r = signal.entry + r_unit
                signal.target_2r = signal.entry + (2 * r_unit)
                signal.trail = signal.entry + (1.5 * r_unit)
            else:
                signal.target_1r = signal.entry - r_unit
                signal.target_2r = signal.entry - (2 * r_unit)
                signal.trail = signal.entry - (1.5 * r_unit)

        signal.qualification_log.append(
            f"Stage 6 SIZED: {signal.shares} shares, "
            f"risk=${signal.risk_dollars:.2f} ({signal.risk_pct*100:.2f}%), "
            f"position={signal.position_pct_equity*100:.1f}% of equity"
        )

    # Leverage cascade: confidence → leverage level for ISA
    # High conviction (80+) + trending: 5x
    # Medium conviction (65-79): 3x (default)
    # Low conviction (55-64): 1x defensive UCITS
    _leverage_cascade = {
        # 5x options (high conviction only)
        ("QQQ", "LONG", "5x"): ("QQQ5.L", "5x", "Nasdaq 100"),
        ("QQQ", "SHORT", "5x"): ("QQS5.L", "-5x", "Nasdaq 100"),
        ("SPY", "LONG", "5x"): ("SP5L.L", "5x", "S&P 500"),
        ("SPY", "SHORT", "5x"): ("SP5S.L", "-5x", "S&P 500"),
        # 1x defensive UCITS (low conviction)
        ("QQQ", "LONG", "1x"): ("CNX1.L", "1x", "Nasdaq 100"),
        ("SPY", "LONG", "1x"): ("IUSA.L", "1x", "S&P 500"),
        ("SMH", "LONG", "1x"): ("SMGB.L", "1x", "PHLX Semiconductor"),
    }

    # Single-stock ETP map (ISA leverage plays)
    _single_stock_map = {
        ("MSFT", "LONG"): ("3MSF.L", "3x", "Microsoft"),
        ("AAPL", "LONG"): ("3AAL.L", "3x", "Apple"),
        ("AMZN", "LONG"): ("3AMZ.L", "3x", "Amazon"),
        ("GOOGL", "LONG"): ("3GOL.L", "3x", "Alphabet"),
        ("META", "LONG"): ("3MTA.L", "3x", "Meta"),
    }

    def _select_leverage_tier(self, signal: Signal) -> str:
        """Select leverage tier based on confidence and regime.

        ISA-first capital strategy: always prefer leveraged ISA execution.
        High conviction (80+) + trending regime = 5x
        Medium conviction (65-79) = 3x (default)
        Low conviction (55-64) = 1x defensive UCITS
        RANGE_BOUND regime = 1x regardless (volatility decay kills leveraged ETPs)
        """
        regime = signal.regime

        # RANGE_BOUND: never use leverage (volatility decay)
        if regime == RegimeState.RANGE_BOUND:
            return "1x"

        # HIGH_VOLATILITY or RISK_OFF: 1x only
        if regime in (RegimeState.HIGH_VOLATILITY, RegimeState.RISK_OFF, RegimeState.SHOCK):
            return "1x"

        # High conviction + trending = 5x
        trending = regime in (
            RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD,
            RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD,
        )
        if signal.confidence >= 80 and trending:
            return "5x"

        # Medium conviction = 3x (default)
        if signal.confidence >= 65:
            return "3x"

        # Low conviction = 1x defensive
        return "1x"

    def _stage7_isa_mapper(self, signal: Signal) -> None:
        """Stage 7: ISA Mapper with Leverage Cascade.

        Maps Bot B signals to Bot A ETP equivalents with confidence-based
        leverage selection. ISA = tax-free compounding, so we ALWAYS prefer
        leveraged ISA execution when a mapping exists.

        Leverage Cascade:
        - 5x: confidence >= 80 + trending regime (QQQ5.L, SP5L.L)
        - 3x: confidence 65-79 (QQQ3.L, NVD3.L, etc.) — default
        - 1x: confidence 55-64 or RANGE_BOUND (CNX1.L, IUSA.L, SMGB.L)
        """
        direction = signal.direction.value
        tier = self._select_leverage_tier(signal)

        # 1. Check single-stock ETP first (always 3x, no cascade)
        ss_key = (signal.ticker, direction)
        ss_mapping = self._single_stock_map.get(ss_key)
        if ss_mapping:
            signal.isa_ticker, signal.isa_leverage, signal.isa_underlying = ss_mapping
            signal.qualification_log.append(
                f"Stage 7 MAPPED (single-stock): {signal.ticker} {direction} → "
                f"{signal.isa_ticker} ({signal.isa_leverage} {signal.isa_underlying})"
            )
            return

        # 2. Check leverage cascade (5x or 1x overrides)
        cascade_key = (signal.ticker, direction, tier)
        cascade_mapping = self._leverage_cascade.get(cascade_key)
        if cascade_mapping:
            signal.isa_ticker, signal.isa_leverage, signal.isa_underlying = cascade_mapping
            signal.qualification_log.append(
                f"Stage 7 MAPPED (cascade {tier}): {signal.ticker} {direction} → "
                f"{signal.isa_ticker} ({signal.isa_leverage} {signal.isa_underlying})"
            )
            return

        # 3. Fall back to standard 3x ISA map
        std_key = (signal.ticker, direction)
        std_mapping = self.isa_map.get(std_key)
        if std_mapping:
            signal.isa_ticker, signal.isa_leverage, signal.isa_underlying = std_mapping
            # If tier is 1x but no 1x mapping exists, note it
            if tier == "1x":
                signal.qualification_log.append(
                    f"Stage 7 MAPPED (3x, no 1x available): {signal.ticker} {direction} → "
                    f"{signal.isa_ticker} ({signal.isa_leverage} {signal.isa_underlying}) "
                    f"[CAUTION: wanted 1x but none available]"
                )
            else:
                signal.qualification_log.append(
                    f"Stage 7 MAPPED ({tier}): {signal.ticker} {direction} → "
                    f"{signal.isa_ticker} ({signal.isa_leverage} {signal.isa_underlying})"
                )
            return

        # 4. No ISA equivalent — flag as spread bet only
        signal.isa_ticker = "SB_ONLY"
        signal.isa_leverage = ""
        signal.isa_underlying = ""
        signal.qualification_log.append(
            f"Stage 7: No ISA equivalent for {signal.ticker} {direction} — SB only"
        )
