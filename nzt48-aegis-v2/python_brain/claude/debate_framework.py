"""claude/debate_framework.py — Book 62: 7-Agent Debate Framework.

Multi-agent debate system for trade signal evaluation. Seven specialized
agents analyze each signal from different perspectives, then a synthesis
agent produces a final EXECUTE / SKIP / REDUCE decision.

Agents:
  REGIME    — Market regime compatibility (trending/mean-rev/volatile)
  ALPHA     — Expected alpha and edge quality
  RISK      — Position risk and portfolio impact
  TIMING    — Entry timing quality (microstructure, session, momentum)
  COST      — Transaction cost impact (spread, slippage, commission)
  CAPACITY  — Liquidity and capacity constraints
  SYNTHESIS — Weighted aggregation of all agent opinions

Agent weights (Book 62):
  RISK=0.25, ALPHA=0.20, REGIME=0.15, TIMING=0.15, COST=0.15, CAPACITY=0.10

Each agent can use AI (Claude/Gemini) or fall back to rule-based heuristics
when AI infrastructure is unavailable. The fallback ensures the debate
framework is always functional, even on cold start or API failure.

Credibility tracking: EMA of each agent's historical accuracy, persisted
to /app/data/debate_credibility.json. Agents that consistently produce
good recommendations get higher effective weight over time.

Bridge.py integration:
    from python_brain.claude.debate_framework import DebateFramework, DebateConfig

    # In bridge.py init:
    try:
        from python_brain.claude.debate_framework import DebateFramework, DebateConfig
        _debate = DebateFramework(DebateConfig())
    except ImportError:
        _debate = None

    # On signal evaluation:
    if _debate and signal_confidence >= 60:
        result = _debate.evaluate_signal(signal_data)
        if result.final_decision == "SKIP":
            return no_signal(ticker_id)
        if result.final_decision == "REDUCE":
            signal["kelly"] *= 0.5
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Optional AI dispatchers — fail-open if not available on EC2
try:
    from python_brain.claude.decision_authority import DecisionAuthority
except ImportError:
    DecisionAuthority = None  # type: ignore[assignment,misc]

__all__ = [
    "AgentRole",
    "AgentResponse",
    "DebateConfig",
    "DebateRound",
    "DebateFramework",
]

# ── Persistence Paths ──────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CREDIBILITY_PATH = DATA_DIR / "debate_credibility.json"
DEBATE_LOG_PATH = DATA_DIR / "debate_log.ndjson"


# ── Enums & Dataclasses ───────────────────────────────────────────────

class AgentRole(Enum):
    """The 7 debate agents."""
    REGIME = "regime"
    ALPHA = "alpha"
    RISK = "risk"
    TIMING = "timing"
    COST = "cost"
    CAPACITY = "capacity"
    SYNTHESIS = "synthesis"


# Agent weights for final synthesis (Book 62)
AGENT_WEIGHTS: Dict[AgentRole, float] = {
    AgentRole.RISK: 0.25,
    AgentRole.ALPHA: 0.20,
    AgentRole.REGIME: 0.15,
    AgentRole.TIMING: 0.15,
    AgentRole.COST: 0.15,
    AgentRole.CAPACITY: 0.10,
}


@dataclass
class AgentResponse:
    """A single agent's response in the debate."""
    role: AgentRole
    timestamp: str = ""
    score: float = 0.5       # 0.0 = strong reject, 1.0 = strong approve
    confidence: float = 0.5  # 0.0 = uncertain, 1.0 = highly confident
    recommendation: str = ""  # Short text: "EXECUTE", "SKIP", "REDUCE", etc.
    reasoning: str = ""      # Explanation
    red_flags: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        return d


@dataclass
class DebateConfig:
    """Configuration for the debate framework."""
    enabled: bool = True
    timeout_seconds: float = 5.0       # Max time per agent
    min_agents: int = 3                # Minimum agents required for valid debate
    confidence_threshold: float = 0.6  # Below this → SKIP
    cost_budget_per_debate: float = 0.01  # USD budget per debate round
    use_ai: bool = False               # Whether to attempt AI-based agents
    execute_threshold: float = 0.60    # Score above this → EXECUTE
    reduce_threshold: float = 0.45     # Score between reduce and execute → REDUCE
    # Below reduce_threshold → SKIP
    credibility_ema_alpha: float = 0.1  # EMA smoothing for credibility updates


@dataclass
class DebateRound:
    """Result of a full debate round."""
    signal_id: str = ""
    timestamp: str = ""
    agent_responses: List[AgentResponse] = field(default_factory=list)
    synthesis: Dict[str, Any] = field(default_factory=dict)
    final_decision: str = "SKIP"   # EXECUTE, SKIP, REDUCE
    confidence: float = 0.0
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "timestamp": self.timestamp,
            "agent_responses": [r.to_dict() for r in self.agent_responses],
            "synthesis": self.synthesis,
            "final_decision": self.final_decision,
            "confidence": round(self.confidence, 4),
            "latency_ms": round(self.latency_ms, 2),
        }


# ── Debate Framework ──────────────────────────────────────────────────

class DebateFramework:
    """7-agent debate framework for trade signal evaluation.

    Runs each agent (REGIME, ALPHA, RISK, TIMING, COST, CAPACITY) on the
    signal data, then synthesizes a final decision. Agents can use AI
    dispatchers or fall back to rule-based heuristics.
    """

    def __init__(self, config: Optional[DebateConfig] = None,
                 claude_dispatcher: Any = None,
                 gemini_scanner: Any = None):
        """Initialize the debate framework.

        Args:
            config: Debate configuration. Uses defaults if None.
            claude_dispatcher: Optional Claude AI dispatcher for AI-powered agents.
            gemini_scanner: Optional Gemini scanner for AI-powered agents.
        """
        self.config = config or DebateConfig()
        self._claude = claude_dispatcher
        self._gemini = gemini_scanner
        self._credibility: Dict[str, float] = {}
        self._load_credibility()

    def evaluate_signal(self, signal_data: Dict[str, Any]) -> DebateRound:
        """Run the full 7-agent debate on a trade signal.

        Args:
            signal_data: Signal dict with keys like ticker, direction,
                confidence, strategy, price, volume, regime, etc.

        Returns:
            DebateRound with all agent responses and final decision.
        """
        if not self.config.enabled:
            return DebateRound(
                signal_id=signal_data.get("signal_id", ""),
                timestamp=datetime.now(timezone.utc).isoformat(),
                final_decision="EXECUTE",
                confidence=1.0,
            )

        t0 = time.monotonic()
        signal_id = signal_data.get("signal_id", f"sig_{int(time.time())}")

        # Run all 6 evaluation agents (SYNTHESIS is not an evaluation agent)
        responses: List[AgentResponse] = []

        # Extract regime context for downstream agents
        regime_resp = self._run_regime_agent(signal_data)
        responses.append(regime_resp)
        regime_context = {
            "regime_score": regime_resp.score,
            "regime_flags": regime_resp.red_flags,
            "regime_data": regime_resp.data,
        }

        # Run remaining agents with regime context
        portfolio_state = signal_data.get("portfolio", {})
        responses.append(self._run_alpha_agent(signal_data, regime_context))
        responses.append(self._run_risk_agent(signal_data, portfolio_state))
        responses.append(self._run_timing_agent(signal_data))
        responses.append(self._run_cost_agent(signal_data))
        responses.append(self._run_capacity_agent(signal_data))

        # Check minimum agent threshold
        valid_responses = [r for r in responses if r.confidence > 0.0]
        if len(valid_responses) < self.config.min_agents:
            log.warning("Only %d/%d agents responded — insufficient for debate",
                        len(valid_responses), self.config.min_agents)
            result = DebateRound(
                signal_id=signal_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_responses=responses,
                final_decision="SKIP",
                confidence=0.0,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
            self._log_debate(result)
            return result

        # Synthesize
        synthesis = self._synthesize(responses)
        final_decision, final_confidence = self._compute_final_decision(synthesis)

        result = DebateRound(
            signal_id=signal_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_responses=responses,
            synthesis=synthesis,
            final_decision=final_decision,
            confidence=final_confidence,
            latency_ms=(time.monotonic() - t0) * 1000,
        )

        self._log_debate(result)
        return result

    def update_credibility(self, role: AgentRole, was_correct: bool) -> None:
        """Update an agent's credibility score based on outcome.

        Uses EMA: credibility = alpha * outcome + (1-alpha) * old_credibility

        Args:
            role: Which agent to update.
            was_correct: Whether the agent's recommendation aligned with outcome.
        """
        alpha = self.config.credibility_ema_alpha
        key = role.value
        old = self._credibility.get(key, 0.5)
        outcome = 1.0 if was_correct else 0.0
        new = alpha * outcome + (1.0 - alpha) * old
        self._credibility[key] = round(new, 6)
        self._save_credibility()
        log.debug("Credibility update: %s %.4f → %.4f (correct=%s)",
                  role.value, old, new, was_correct)

    # ── Agent Implementations ──────────────────────────────────────────

    def _run_regime_agent(self, signal_data: Dict[str, Any]) -> AgentResponse:
        """REGIME agent: Evaluate market regime compatibility.

        Checks if the signal's strategy is appropriate for the current
        market regime. A trend-following signal in a mean-reverting market
        gets a low score.
        """
        t0 = time.monotonic()
        try:
            regime = signal_data.get("regime", "unknown")
            direction = signal_data.get("direction", "long")
            strategy = signal_data.get("strategy", "")
            vix = signal_data.get("vix", 20.0)
            hmm_state = signal_data.get("hmm_state", -1)
            hurst = signal_data.get("hurst", 0.5)

            score = 0.5
            red_flags: List[str] = []
            reasoning_parts: List[str] = []

            # Regime-strategy compatibility matrix
            trend_strategies = {"VanguardSniper", "S3_MacroTrend", "S5_OvernightCarry"}
            reversion_strategies = {"S2_Reversion", "ApexScout"}
            vol_strategies = {"S4_VolPremium", "S7_TailHedge"}

            if regime in ("trending", "trend_up", "trend_down"):
                if strategy in trend_strategies:
                    score += 0.25
                    reasoning_parts.append("Trend strategy in trending regime: compatible")
                elif strategy in reversion_strategies:
                    score -= 0.20
                    red_flags.append("Reversion strategy in trending regime")
                    reasoning_parts.append("Reversion strategy fights the trend")
                else:
                    score += 0.05
                    reasoning_parts.append("Neutral strategy-regime alignment")

            elif regime in ("mean_reverting", "range", "choppy"):
                if strategy in reversion_strategies:
                    score += 0.25
                    reasoning_parts.append("Reversion strategy in ranging regime: compatible")
                elif strategy in trend_strategies:
                    score -= 0.20
                    red_flags.append("Trend strategy in choppy regime")
                    reasoning_parts.append("Trend strategy likely to get whipsawed")
                else:
                    score += 0.05

            elif regime in ("volatile", "crisis"):
                if strategy in vol_strategies:
                    score += 0.20
                    reasoning_parts.append("Vol strategy in volatile regime")
                else:
                    score -= 0.15
                    red_flags.append("Non-vol strategy during high volatility")

            # VIX adjustment
            if vix > 35:
                score -= 0.10
                red_flags.append(f"VIX elevated at {vix:.1f}")
            elif vix < 12:
                score += 0.05
                reasoning_parts.append("Low VIX environment")

            # Hurst exponent: >0.5 = trending, <0.5 = mean-reverting
            if hurst > 0.6 and strategy in reversion_strategies:
                score -= 0.10
                red_flags.append(f"Hurst={hurst:.2f} suggests trend, not reversion")
            elif hurst < 0.4 and strategy in trend_strategies:
                score -= 0.10
                red_flags.append(f"Hurst={hurst:.2f} suggests mean-reversion, not trend")

            # HMM state: negative = unknown, 0 = bullish, 1 = bearish, 2 = neutral
            if hmm_state == 1 and direction == "long":
                score -= 0.10
                red_flags.append("HMM bearish but signal is long")
            elif hmm_state == 0 and direction == "short":
                score -= 0.10
                red_flags.append("HMM bullish but signal is short")

            score = float(np.clip(score, 0.0, 1.0))
            confidence = 0.7 if regime != "unknown" else 0.3

            return AgentResponse(
                role=AgentRole.REGIME,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=score,
                confidence=confidence,
                recommendation="EXECUTE" if score > 0.5 else "SKIP",
                reasoning="; ".join(reasoning_parts) or "No strong regime signal",
                red_flags=red_flags,
                data={
                    "regime": regime,
                    "vix": vix,
                    "hurst": hurst,
                    "hmm_state": hmm_state,
                    "latency_ms": (time.monotonic() - t0) * 1000,
                },
            )
        except Exception as e:
            log.error("REGIME agent failed: %s", e)
            return AgentResponse(
                role=AgentRole.REGIME,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=0.5,
                confidence=0.1,
                recommendation="PASS",
                reasoning=f"Agent error: {e}",
            )

    def _run_alpha_agent(self, signal_data: Dict[str, Any],
                          regime_context: Dict[str, Any]) -> AgentResponse:
        """ALPHA agent: Evaluate expected alpha and edge quality.

        Examines signal confidence, historical strategy performance,
        and whether the edge is statistically significant.
        """
        t0 = time.monotonic()
        try:
            confidence_raw = signal_data.get("confidence", 50)
            strategy = signal_data.get("strategy", "")
            win_rate = signal_data.get("strategy_win_rate", 0.5)
            profit_factor = signal_data.get("strategy_profit_factor", 1.0)
            n_trades = signal_data.get("strategy_n_trades", 0)
            kelly = signal_data.get("kelly", 0.10)
            edge_zscore = signal_data.get("edge_zscore", 0.0)

            score = 0.5
            red_flags: List[str] = []
            reasoning_parts: List[str] = []

            # Confidence quality
            if confidence_raw >= 75:
                score += 0.20
                reasoning_parts.append(f"High confidence ({confidence_raw})")
            elif confidence_raw >= 60:
                score += 0.10
                reasoning_parts.append(f"Moderate confidence ({confidence_raw})")
            elif confidence_raw < 55:
                score -= 0.15
                red_flags.append(f"Low confidence ({confidence_raw})")

            # Historical win rate
            if n_trades >= 30:
                if win_rate > 0.55:
                    score += 0.15
                    reasoning_parts.append(f"Proven win rate {win_rate:.1%} over {n_trades} trades")
                elif win_rate < 0.45:
                    score -= 0.15
                    red_flags.append(f"Poor win rate {win_rate:.1%} over {n_trades} trades")
            else:
                # Insufficient sample — moderate penalty
                score -= 0.05
                reasoning_parts.append(f"Limited history: {n_trades} trades")

            # Profit factor
            if profit_factor > 1.5:
                score += 0.10
                reasoning_parts.append(f"Strong profit factor ({profit_factor:.2f})")
            elif profit_factor < 1.0:
                score -= 0.15
                red_flags.append(f"Unprofitable strategy (PF={profit_factor:.2f})")

            # Kelly sizing quality
            if kelly > 0.25:
                score += 0.05
                reasoning_parts.append(f"High Kelly ({kelly:.2f})")
            elif kelly < 0.05:
                score -= 0.10
                red_flags.append(f"Tiny Kelly ({kelly:.2f}) — edge may be noise")

            # Statistical significance of edge
            if edge_zscore > 2.0:
                score += 0.10
                reasoning_parts.append(f"Edge z-score={edge_zscore:.1f} (significant)")
            elif edge_zscore < 1.0 and n_trades > 50:
                score -= 0.10
                red_flags.append(f"Edge z-score={edge_zscore:.1f} (not significant)")

            # Regime compatibility from REGIME agent
            regime_score = regime_context.get("regime_score", 0.5)
            if regime_score < 0.4:
                score -= 0.05
                reasoning_parts.append("Regime compatibility is poor — alpha at risk")

            score = float(np.clip(score, 0.0, 1.0))
            confidence = min(0.8, 0.3 + (n_trades / 200.0))  # More trades → higher confidence

            return AgentResponse(
                role=AgentRole.ALPHA,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=score,
                confidence=confidence,
                recommendation="EXECUTE" if score > 0.55 else ("REDUCE" if score > 0.40 else "SKIP"),
                reasoning="; ".join(reasoning_parts) or "No strong alpha signal",
                red_flags=red_flags,
                data={
                    "confidence_raw": confidence_raw,
                    "win_rate": win_rate,
                    "profit_factor": profit_factor,
                    "n_trades": n_trades,
                    "kelly": kelly,
                    "latency_ms": (time.monotonic() - t0) * 1000,
                },
            )
        except Exception as e:
            log.error("ALPHA agent failed: %s", e)
            return AgentResponse(
                role=AgentRole.ALPHA,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=0.5,
                confidence=0.1,
                recommendation="PASS",
                reasoning=f"Agent error: {e}",
            )

    def _run_risk_agent(self, signal_data: Dict[str, Any],
                         portfolio_state: Dict[str, Any]) -> AgentResponse:
        """RISK agent: Evaluate position risk and portfolio impact.

        Checks position sizing, correlation with existing positions,
        portfolio concentration, and drawdown proximity.
        """
        t0 = time.monotonic()
        try:
            kelly = signal_data.get("kelly", 0.10)
            price = signal_data.get("price", 0.0)
            shares = signal_data.get("shares", 0)
            ticker = signal_data.get("ticker", "")
            direction = signal_data.get("direction", "long")

            # Portfolio state
            n_positions = portfolio_state.get("n_positions", 0)
            max_positions = portfolio_state.get("max_positions", 10)
            current_drawdown_pct = portfolio_state.get("drawdown_pct", 0.0)
            max_drawdown_limit = portfolio_state.get("max_drawdown_pct", 5.0)
            sector_exposure = portfolio_state.get("sector_exposure", {})
            daily_loss_pct = portfolio_state.get("daily_loss_pct", 0.0)
            open_tickers = portfolio_state.get("open_tickers", [])

            score = 0.5
            red_flags: List[str] = []
            reasoning_parts: List[str] = []

            # Position concentration
            if n_positions >= max_positions:
                score -= 0.30
                red_flags.append(f"At max positions ({n_positions}/{max_positions})")
            elif n_positions >= max_positions * 0.8:
                score -= 0.10
                reasoning_parts.append(f"Near position limit ({n_positions}/{max_positions})")
            else:
                score += 0.10
                reasoning_parts.append(f"Position capacity OK ({n_positions}/{max_positions})")

            # Drawdown check
            if current_drawdown_pct > max_drawdown_limit * 0.8:
                score -= 0.25
                red_flags.append(f"Near drawdown limit ({current_drawdown_pct:.1f}%/{max_drawdown_limit:.1f}%)")
            elif current_drawdown_pct > max_drawdown_limit * 0.5:
                score -= 0.10
                reasoning_parts.append("Moderate drawdown — caution")
            else:
                score += 0.10
                reasoning_parts.append("Drawdown within comfortable range")

            # Daily loss check
            if daily_loss_pct > 1.5:
                score -= 0.20
                red_flags.append(f"Heavy daily loss ({daily_loss_pct:.1f}%)")
            elif daily_loss_pct > 0.5:
                score -= 0.05

            # Duplicate ticker check
            if ticker in open_tickers:
                score -= 0.15
                red_flags.append(f"Already have open position in {ticker}")

            # Kelly sizing sanity
            if kelly > 0.30:
                score -= 0.05
                reasoning_parts.append(f"High Kelly ({kelly:.2f}) — verify sizing")
            elif kelly < 0.05:
                score += 0.05
                reasoning_parts.append("Conservative Kelly — low risk")

            # Notional value check
            notional = price * shares if price > 0 and shares > 0 else 0
            if notional > 2000:  # ISA max single position ~GBP 2000
                score -= 0.10
                red_flags.append(f"Large notional (GBP {notional:.0f})")

            score = float(np.clip(score, 0.0, 1.0))
            confidence = 0.7 if portfolio_state else 0.4

            return AgentResponse(
                role=AgentRole.RISK,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=score,
                confidence=confidence,
                recommendation="EXECUTE" if score > 0.55 else ("REDUCE" if score > 0.35 else "SKIP"),
                reasoning="; ".join(reasoning_parts) or "Standard risk assessment",
                red_flags=red_flags,
                data={
                    "n_positions": n_positions,
                    "drawdown_pct": current_drawdown_pct,
                    "notional": notional,
                    "kelly": kelly,
                    "latency_ms": (time.monotonic() - t0) * 1000,
                },
            )
        except Exception as e:
            log.error("RISK agent failed: %s", e)
            return AgentResponse(
                role=AgentRole.RISK,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=0.5,
                confidence=0.1,
                recommendation="PASS",
                reasoning=f"Agent error: {e}",
            )

    def _run_timing_agent(self, signal_data: Dict[str, Any]) -> AgentResponse:
        """TIMING agent: Evaluate entry timing quality.

        Checks session timing, momentum alignment, spread conditions,
        and microstructure signals.
        """
        t0 = time.monotonic()
        try:
            session = signal_data.get("session", "unknown")
            momentum_aligned = signal_data.get("momentum_aligned", True)
            spread_pct = signal_data.get("spread_pct", 0.10)
            rvol = signal_data.get("rvol", 1.0)  # Relative volume
            vwap_distance_pct = signal_data.get("vwap_distance_pct", 0.0)
            rsi = signal_data.get("rsi", 50.0)
            direction = signal_data.get("direction", "long")
            bar_count = signal_data.get("bar_count", 0)  # How many bars since session open
            time_of_day = signal_data.get("time_of_day", "")  # HH:MM

            score = 0.5
            red_flags: List[str] = []
            reasoning_parts: List[str] = []

            # Session quality
            good_sessions = {"us_morning", "us_open", "london_morning", "overlap"}
            bad_sessions = {"pre_market", "after_hours", "lunch"}
            if session in good_sessions:
                score += 0.15
                reasoning_parts.append(f"Good session timing ({session})")
            elif session in bad_sessions:
                score -= 0.15
                red_flags.append(f"Poor session timing ({session})")

            # Momentum alignment
            if momentum_aligned:
                score += 0.10
                reasoning_parts.append("Momentum aligned with signal direction")
            else:
                score -= 0.15
                red_flags.append("Momentum diverges from signal direction")

            # Spread condition
            if spread_pct > 0.30:
                score -= 0.20
                red_flags.append(f"Wide spread ({spread_pct:.2f}%)")
            elif spread_pct > 0.15:
                score -= 0.05
                reasoning_parts.append(f"Moderate spread ({spread_pct:.2f}%)")
            else:
                score += 0.10
                reasoning_parts.append(f"Tight spread ({spread_pct:.2f}%)")

            # Relative volume
            if rvol > 2.0:
                score += 0.10
                reasoning_parts.append(f"High relative volume ({rvol:.1f}x)")
            elif rvol < 0.5:
                score -= 0.10
                red_flags.append(f"Low relative volume ({rvol:.1f}x)")

            # VWAP distance (entry quality)
            if direction == "long" and vwap_distance_pct < -0.5:
                score += 0.05
                reasoning_parts.append("Below VWAP — good long entry")
            elif direction == "long" and vwap_distance_pct > 1.0:
                score -= 0.10
                red_flags.append(f"Far above VWAP ({vwap_distance_pct:.1f}%) — chasing")
            elif direction == "short" and vwap_distance_pct > 0.5:
                score += 0.05
                reasoning_parts.append("Above VWAP — good short entry")

            # RSI extremes (overbought/oversold timing)
            if direction == "long" and rsi > 80:
                score -= 0.10
                red_flags.append(f"RSI overbought ({rsi:.0f})")
            elif direction == "short" and rsi < 20:
                score -= 0.10
                red_flags.append(f"RSI oversold ({rsi:.0f})")

            # Too early / too late in session
            if bar_count < 5:
                score -= 0.05
                reasoning_parts.append("Very early in session — limited price discovery")
            elif bar_count > 400:
                score -= 0.05
                reasoning_parts.append("Late session — limited time for trade to develop")

            score = float(np.clip(score, 0.0, 1.0))
            confidence = 0.6

            return AgentResponse(
                role=AgentRole.TIMING,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=score,
                confidence=confidence,
                recommendation="EXECUTE" if score > 0.55 else ("REDUCE" if score > 0.40 else "SKIP"),
                reasoning="; ".join(reasoning_parts) or "Neutral timing",
                red_flags=red_flags,
                data={
                    "session": session,
                    "spread_pct": spread_pct,
                    "rvol": rvol,
                    "rsi": rsi,
                    "latency_ms": (time.monotonic() - t0) * 1000,
                },
            )
        except Exception as e:
            log.error("TIMING agent failed: %s", e)
            return AgentResponse(
                role=AgentRole.TIMING,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=0.5,
                confidence=0.1,
                recommendation="PASS",
                reasoning=f"Agent error: {e}",
            )

    def _run_cost_agent(self, signal_data: Dict[str, Any]) -> AgentResponse:
        """COST agent: Evaluate transaction cost impact on expected profit.

        Computes the cost-to-edge ratio. If transaction costs eat most
        of the expected edge, the trade isn't worth taking.
        """
        t0 = time.monotonic()
        try:
            spread_pct = signal_data.get("spread_pct", 0.10)
            commission_pct = signal_data.get("commission_pct", 0.0)
            slippage_pct = signal_data.get("slippage_est_pct", 0.02)
            expected_edge_pct = signal_data.get("expected_edge_pct", 0.30)
            shares = signal_data.get("shares", 0)
            price = signal_data.get("price", 0.0)
            holding_period_bars = signal_data.get("holding_period_bars", 50)

            # Total round-trip cost
            total_cost_pct = (spread_pct + commission_pct + slippage_pct) * 2.0  # Entry + exit

            # Cost-to-edge ratio
            if expected_edge_pct > 0:
                cost_edge_ratio = total_cost_pct / expected_edge_pct
            else:
                cost_edge_ratio = float("inf")

            score = 0.5
            red_flags: List[str] = []
            reasoning_parts: List[str] = []

            # Absolute cost threshold
            if total_cost_pct > 0.80:
                score -= 0.30
                red_flags.append(f"Extremely high round-trip cost ({total_cost_pct:.2f}%)")
            elif total_cost_pct > 0.40:
                score -= 0.15
                red_flags.append(f"High round-trip cost ({total_cost_pct:.2f}%)")
            elif total_cost_pct < 0.10:
                score += 0.20
                reasoning_parts.append(f"Low transaction costs ({total_cost_pct:.2f}%)")
            else:
                score += 0.05
                reasoning_parts.append(f"Acceptable costs ({total_cost_pct:.2f}%)")

            # Cost-to-edge ratio (the critical metric)
            if cost_edge_ratio > 0.80:
                score -= 0.25
                red_flags.append(f"Costs consume {cost_edge_ratio:.0%} of expected edge")
            elif cost_edge_ratio > 0.50:
                score -= 0.10
                reasoning_parts.append(f"Costs consume {cost_edge_ratio:.0%} of edge")
            elif cost_edge_ratio < 0.20:
                score += 0.15
                reasoning_parts.append(f"Excellent cost-to-edge ratio ({cost_edge_ratio:.0%})")
            else:
                score += 0.05

            # Spread drag for short holding periods
            if holding_period_bars < 20 and spread_pct > 0.15:
                score -= 0.10
                red_flags.append("Short hold + wide spread — spread drag dominates")

            # Notional too small (fixed costs dominate)
            notional = price * shares if price > 0 and shares > 0 else 0
            if 0 < notional < 100:
                score -= 0.10
                red_flags.append(f"Small notional ({notional:.0f}) — fixed costs dominate")

            score = float(np.clip(score, 0.0, 1.0))
            confidence = 0.7 if spread_pct > 0 else 0.3

            return AgentResponse(
                role=AgentRole.COST,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=score,
                confidence=confidence,
                recommendation="EXECUTE" if score > 0.55 else ("REDUCE" if score > 0.40 else "SKIP"),
                reasoning="; ".join(reasoning_parts) or "Cost assessment neutral",
                red_flags=red_flags,
                data={
                    "total_cost_pct": round(total_cost_pct, 4),
                    "cost_edge_ratio": round(min(cost_edge_ratio, 99.0), 4),
                    "spread_pct": spread_pct,
                    "notional": notional,
                    "latency_ms": (time.monotonic() - t0) * 1000,
                },
            )
        except Exception as e:
            log.error("COST agent failed: %s", e)
            return AgentResponse(
                role=AgentRole.COST,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=0.5,
                confidence=0.1,
                recommendation="PASS",
                reasoning=f"Agent error: {e}",
            )

    def _run_capacity_agent(self, signal_data: Dict[str, Any]) -> AgentResponse:
        """CAPACITY agent: Evaluate liquidity and capacity constraints.

        Checks average daily volume, relative order size, and
        market impact estimates.
        """
        t0 = time.monotonic()
        try:
            shares = signal_data.get("shares", 0)
            avg_daily_volume = signal_data.get("avg_daily_volume", 0)
            rvol = signal_data.get("rvol", 1.0)
            bid_ask_depth = signal_data.get("bid_ask_depth", 0)
            price = signal_data.get("price", 0.0)
            market_cap = signal_data.get("market_cap", 0)

            score = 0.5
            red_flags: List[str] = []
            reasoning_parts: List[str] = []

            # Volume participation rate
            if avg_daily_volume > 0 and shares > 0:
                participation = shares / avg_daily_volume
                if participation > 0.05:
                    score -= 0.30
                    red_flags.append(f"Order is {participation:.1%} of ADV — market impact risk")
                elif participation > 0.01:
                    score -= 0.10
                    reasoning_parts.append(f"Moderate volume participation ({participation:.2%} of ADV)")
                else:
                    score += 0.15
                    reasoning_parts.append(f"Low volume participation ({participation:.3%} of ADV)")
            elif shares > 0:
                # No volume data — cautious
                score -= 0.05
                reasoning_parts.append("No ADV data available")

            # Absolute liquidity
            if avg_daily_volume > 1_000_000:
                score += 0.10
                reasoning_parts.append("High liquidity (ADV > 1M)")
            elif avg_daily_volume > 100_000:
                score += 0.05
            elif 0 < avg_daily_volume < 50_000:
                score -= 0.15
                red_flags.append(f"Low liquidity (ADV={avg_daily_volume:,})")

            # Relative volume check
            if rvol < 0.3:
                score -= 0.10
                red_flags.append(f"Very low relative volume ({rvol:.1f}x) — illiquid session")

            # Market cap filter
            if 0 < market_cap < 100_000_000:  # Sub-100M = micro-cap
                score -= 0.10
                red_flags.append("Micro-cap — capacity constraints likely")
            elif market_cap > 10_000_000_000:  # Large-cap
                score += 0.10
                reasoning_parts.append("Large-cap — ample capacity")

            # Price level (penny stock risk)
            if 0 < price < 1.0:
                score -= 0.15
                red_flags.append(f"Low price ({price:.2f}) — penny stock liquidity risk")

            score = float(np.clip(score, 0.0, 1.0))
            confidence = 0.6 if avg_daily_volume > 0 else 0.3

            return AgentResponse(
                role=AgentRole.CAPACITY,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=score,
                confidence=confidence,
                recommendation="EXECUTE" if score > 0.55 else ("REDUCE" if score > 0.40 else "SKIP"),
                reasoning="; ".join(reasoning_parts) or "Capacity assessment neutral",
                red_flags=red_flags,
                data={
                    "shares": shares,
                    "avg_daily_volume": avg_daily_volume,
                    "rvol": rvol,
                    "latency_ms": (time.monotonic() - t0) * 1000,
                },
            )
        except Exception as e:
            log.error("CAPACITY agent failed: %s", e)
            return AgentResponse(
                role=AgentRole.CAPACITY,
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=0.5,
                confidence=0.1,
                recommendation="PASS",
                reasoning=f"Agent error: {e}",
            )

    # ── Synthesis ──────────────────────────────────────────────────────

    def _synthesize(self, responses: List[AgentResponse]) -> Dict[str, Any]:
        """Weighted synthesis of all agent responses.

        Uses base weights from AGENT_WEIGHTS, modulated by each agent's
        credibility score and per-response confidence.

        Returns:
            Synthesis dict with weighted_score, components, red_flags, etc.
        """
        weighted_score = 0.0
        total_weight = 0.0
        all_red_flags: List[str] = []
        components: Dict[str, Dict[str, float]] = {}

        for resp in responses:
            if resp.role == AgentRole.SYNTHESIS:
                continue  # Skip synthesis agent (it doesn't vote)

            base_weight = AGENT_WEIGHTS.get(resp.role, 0.10)
            credibility = self._credibility.get(resp.role.value, 0.5)

            # Effective weight = base_weight * credibility * response_confidence
            effective_weight = base_weight * credibility * resp.confidence
            weighted_score += resp.score * effective_weight
            total_weight += effective_weight

            components[resp.role.value] = {
                "score": round(resp.score, 4),
                "confidence": round(resp.confidence, 4),
                "base_weight": base_weight,
                "credibility": round(credibility, 4),
                "effective_weight": round(effective_weight, 4),
            }

            # Collect red flags
            for flag in resp.red_flags:
                all_red_flags.append(f"[{resp.role.value.upper()}] {flag}")

        # Normalize
        if total_weight > 0:
            weighted_score /= total_weight
        else:
            weighted_score = 0.5

        # Red flag penalty: each red flag reduces the score slightly
        n_flags = len(all_red_flags)
        flag_penalty = min(0.15, n_flags * 0.02)  # Max 15% penalty from red flags
        adjusted_score = max(0.0, weighted_score - flag_penalty)

        # Unanimous rejection: if all agents score < 0.4, force low score
        agent_scores = [r.score for r in responses if r.role != AgentRole.SYNTHESIS]
        if agent_scores and all(s < 0.4 for s in agent_scores):
            adjusted_score = min(adjusted_score, 0.25)

        # Strong RISK rejection overrides
        risk_responses = [r for r in responses if r.role == AgentRole.RISK]
        if risk_responses and risk_responses[0].score < 0.25:
            adjusted_score = min(adjusted_score, 0.35)
            all_red_flags.insert(0, "[SYNTHESIS] RISK agent hard reject — capping score")

        return {
            "weighted_score": round(weighted_score, 4),
            "adjusted_score": round(adjusted_score, 4),
            "n_agents": len([r for r in responses if r.role != AgentRole.SYNTHESIS]),
            "n_red_flags": n_flags,
            "flag_penalty": round(flag_penalty, 4),
            "red_flags": all_red_flags,
            "components": components,
        }

    def _compute_final_decision(self, synthesis: Dict[str, Any]) -> Tuple[str, float]:
        """Compute final EXECUTE / SKIP / REDUCE decision from synthesis.

        Args:
            synthesis: Output of _synthesize().

        Returns:
            (decision, confidence) tuple.
        """
        score = synthesis.get("adjusted_score", 0.5)
        n_flags = synthesis.get("n_red_flags", 0)

        # Decision thresholds
        if score >= self.config.execute_threshold:
            decision = "EXECUTE"
            confidence = min(0.95, score)
        elif score >= self.config.reduce_threshold:
            decision = "REDUCE"
            confidence = score
        else:
            decision = "SKIP"
            confidence = 1.0 - score  # Higher confidence in skip when score is lower

        # Confidence modulation by red flag count
        if n_flags >= 5:
            confidence *= 0.7
        elif n_flags >= 3:
            confidence *= 0.85

        # Confidence floor
        confidence = max(0.1, min(1.0, confidence))

        return decision, round(confidence, 4)

    # ── Persistence ────────────────────────────────────────────────────

    def _load_credibility(self) -> None:
        """Load agent credibility scores from JSON file."""
        if not CREDIBILITY_PATH.exists():
            # Default credibility: 0.5 (neutral) for all agents
            self._credibility = {role.value: 0.5 for role in AgentRole
                                  if role != AgentRole.SYNTHESIS}
            return

        try:
            with open(CREDIBILITY_PATH, "r") as f:
                data = json.load(f)
            self._credibility = data.get("credibility", {})
            log.info("Loaded credibility scores: %s", self._credibility)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load credibility: %s — using defaults", e)
            self._credibility = {role.value: 0.5 for role in AgentRole
                                  if role != AgentRole.SYNTHESIS}

    def _save_credibility(self) -> None:
        """Persist credibility scores to JSON file."""
        CREDIBILITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {
                "credibility": self._credibility,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(CREDIBILITY_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            log.error("Failed to save credibility: %s", e)

    def _log_debate(self, result: DebateRound) -> None:
        """Append debate result to NDJSON log."""
        DEBATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(DEBATE_LOG_PATH, "a") as f:
                f.write(json.dumps(result.to_dict(), default=str) + "\n")
        except OSError as e:
            log.error("Failed to log debate: %s", e)
