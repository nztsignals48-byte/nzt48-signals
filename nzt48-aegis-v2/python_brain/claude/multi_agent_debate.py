"""9-Agent Adversarial Debate for Signal Vetting — Book 140.

Extends the 7-agent debate framework (Book 62) to 9 agents with a
structured adversarial protocol. Agents challenge each other across
3 rounds before the FundManager synthesises a final verdict.

Agents:
  1. Fundamentals  — Earnings, valuation, sector rotation
  2. Sentiment     — News, social, positioning, flow
  3. Technical     — Price action, indicators, chart patterns
  4. Microstructure — Order book, spread, tick data, dark pools
  5. Regime        — Macro regime, volatility regime, correlation regime
  6. Risk          — Position risk, portfolio risk, tail risk
  7. Researcher    — Academic edge, statistical significance, decay
  8. Trader        — Execution quality, timing, slippage, urgency
  9. FundManager   — Final synthesis, portfolio-level decision

Protocol:
  Round 1 (Analysis):  All 9 agents independently analyse the signal.
  Round 2 (Challenge): Agents challenge each other's Round 1 conclusions.
                        Bull agents challenge bear agents and vice versa.
  Round 3 (Synthesis): FundManager integrates all evidence + challenges.

Credibility tracking: EMA of each agent's historical accuracy.
State: /app/data/debate_credibility.json

Bridge.py integration:
    try:
        from python_brain.claude.multi_agent_debate import (
            NineAgentDebate, DebateProtocol,
        )
        _debate9 = NineAgentDebate()
    except ImportError:
        _debate9 = None

    # On signal evaluation:
    if _debate9 and signal_confidence >= 55:
        result = _debate9.debate(signal_data, protocol=DebateProtocol.ADVERSARIAL)
        if result["final_verdict"] == "VETO":
            return no_signal(ticker_id)
        if result["final_verdict"] == "HOLD":
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

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("multi_agent_debate")

__all__ = [
    "DebateAgent",
    "DebateMessage",
    "DebateProtocol",
    "NineAgentDebate",
]

# ── Persistence Paths ────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CREDIBILITY_PATH = DATA_DIR / "debate_credibility.json"
DEBATE_LOG_PATH = DATA_DIR / "debate_9agent_log.ndjson"


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------

class DebateProtocol(Enum):
    """Debate protocol variants."""
    PARALLEL = "parallel"       # All agents at once (fastest)
    SEQUENTIAL = "sequential"   # Ordered, each sees previous
    ADVERSARIAL = "adversarial"  # Bull vs Bear explicit challenge


@dataclass
class DebateAgent:
    """Configuration for a single debate agent.

    Attributes:
        name: Agent identifier (e.g. "Fundamentals").
        role: Agent's analytical domain.
        model_provider: Which AI model to use ("claude", "gemini", "rule").
        weight: Base importance weight for voting.
        prompt_template: Template for AI-based analysis.
        credibility: Dynamic credibility score (updated by outcomes).
    """
    name: str
    role: str
    model_provider: str = "rule"
    weight: float = 1.0
    prompt_template: str = ""
    credibility: float = 0.5


@dataclass
class DebateMessage:
    """A single message in the debate.

    Attributes:
        agent_name: Which agent produced this message.
        round_num: Debate round (1, 2, or 3).
        verdict: BUY, SELL, HOLD, or VETO.
        confidence: Agent's confidence in verdict (0.0 - 1.0).
        reasoning: Text explanation.
        red_flags: List of concerns identified.
        data: Additional structured data.
    """
    agent_name: str = ""
    round_num: int = 1
    verdict: str = "HOLD"
    confidence: float = 0.5
    reasoning: str = ""
    red_flags: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Agent Definitions
# ---------------------------------------------------------------------------

# The 9 agents with their base weights and roles
AGENT_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "Fundamentals",
        "role": "Earnings, valuation, sector rotation, macro indicators",
        "weight": 0.10,
        "model_provider": "rule",
    },
    {
        "name": "Sentiment",
        "role": "News sentiment, social media, positioning, fund flows",
        "weight": 0.10,
        "model_provider": "rule",
    },
    {
        "name": "Technical",
        "role": "Price action, indicators, chart patterns, support/resistance",
        "weight": 0.15,
        "model_provider": "rule",
    },
    {
        "name": "Microstructure",
        "role": "Order book dynamics, spread analysis, tick data, dark pool activity",
        "weight": 0.10,
        "model_provider": "rule",
    },
    {
        "name": "Regime",
        "role": "Macro regime, volatility regime, correlation regime, HMM state",
        "weight": 0.12,
        "model_provider": "rule",
    },
    {
        "name": "Risk",
        "role": "Position risk, portfolio impact, drawdown, tail risk, correlation",
        "weight": 0.18,
        "model_provider": "rule",
    },
    {
        "name": "Researcher",
        "role": "Statistical significance, alpha decay, academic edge quality",
        "weight": 0.10,
        "model_provider": "rule",
    },
    {
        "name": "Trader",
        "role": "Execution quality, timing, slippage estimate, urgency assessment",
        "weight": 0.10,
        "model_provider": "rule",
    },
    {
        "name": "FundManager",
        "role": "Final synthesis, portfolio-level decision, risk budget allocation",
        "weight": 0.05,
        "model_provider": "rule",
    },
]


# ---------------------------------------------------------------------------
# Nine-Agent Debate System
# ---------------------------------------------------------------------------

class NineAgentDebate:
    """9-agent adversarial debate system for signal vetting.

    Runs a structured 3-round debate protocol where specialised agents
    analyse, challenge, and synthesise to produce a final trading verdict.

    Supports three protocols:
      PARALLEL:    All agents independently, one round.
      SEQUENTIAL:  Agents in order, each sees previous outputs.
      ADVERSARIAL: 3-round bull vs bear debate with synthesis.

    Can optionally use Claude or Gemini AI for agent reasoning.
    Falls back to rule-based heuristics when AI is unavailable.
    """

    def __init__(
        self,
        claude_fn: Optional[Callable] = None,
        gemini_fn: Optional[Callable] = None,
    ) -> None:
        """Initialise the 9-agent debate system.

        Args:
            claude_fn: Optional callback for Claude AI analysis.
                       Signature: claude_fn(prompt: str) -> str
            gemini_fn: Optional callback for Gemini AI analysis.
                       Signature: gemini_fn(prompt: str) -> str
        """
        self._claude_fn = claude_fn
        self._gemini_fn = gemini_fn

        # Initialise agents
        self._agents: List[DebateAgent] = []
        for defn in AGENT_DEFINITIONS:
            agent = DebateAgent(
                name=defn["name"],
                role=defn["role"],
                model_provider=defn.get("model_provider", "rule"),
                weight=defn["weight"],
            )
            self._agents.append(agent)

        # Load credibility scores
        self._credibility: Dict[str, float] = {}
        self._load_credibility()

        # Apply loaded credibility to agents
        for agent in self._agents:
            agent.credibility = self._credibility.get(agent.name, 0.5)

        log.info("NineAgentDebate initialised: %d agents, claude=%s, gemini=%s",
                 len(self._agents),
                 "available" if claude_fn else "unavailable",
                 "available" if gemini_fn else "unavailable")

    def debate(
        self,
        signal_data: Dict[str, Any],
        protocol: DebateProtocol = DebateProtocol.PARALLEL,
    ) -> Dict[str, Any]:
        """Run a full debate on a trade signal.

        Args:
            signal_data: Signal dict with keys like:
                ticker, direction, confidence, strategy, price,
                volume, regime, portfolio, etc.
            protocol: Which debate protocol to use.

        Returns:
            Dict with keys:
              - final_verdict: BUY, SELL, HOLD, or VETO
              - confidence: Overall confidence (0-1)
              - round_1: List of Round 1 messages
              - round_2: List of Round 2 messages (if adversarial)
              - round_3: Synthesis message (if adversarial)
              - vote_breakdown: Per-agent vote summary
              - red_flags: Aggregated red flags
              - latency_ms: Total debate time
              - protocol: Protocol used
              - timestamp: ISO timestamp
        """
        t0 = time.monotonic()

        if protocol == DebateProtocol.PARALLEL:
            result = self._run_parallel(signal_data)
        elif protocol == DebateProtocol.SEQUENTIAL:
            result = self._run_sequential(signal_data)
        elif protocol == DebateProtocol.ADVERSARIAL:
            result = self._run_adversarial(signal_data)
        else:
            result = self._run_parallel(signal_data)

        result["latency_ms"] = round((time.monotonic() - t0) * 1000.0, 2)
        result["protocol"] = protocol.value
        result["timestamp"] = datetime.now(timezone.utc).isoformat()

        self._log_debate(result)
        return result

    # ── Protocol Implementations ────────────────────────────────────

    def _run_parallel(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """PARALLEL protocol: all agents analyse independently."""
        round1 = self._round_1_analysis(signal_data)
        vote_result = self._vote(round1)

        return {
            "final_verdict": vote_result["verdict"],
            "confidence": vote_result["confidence"],
            "round_1": [m.to_dict() for m in round1],
            "round_2": [],
            "round_3": {},
            "vote_breakdown": vote_result,
            "red_flags": vote_result.get("red_flags", []),
        }

    def _run_sequential(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """SEQUENTIAL protocol: ordered analysis, each agent sees previous."""
        messages: List[DebateMessage] = []
        context_so_far: List[Dict[str, Any]] = []

        for agent in self._agents:
            if agent.name == "FundManager":
                continue  # FundManager goes last as synthesiser

            enriched = dict(signal_data)
            enriched["prior_opinions"] = context_so_far

            msg = self._agent_analyse(agent, enriched, round_num=1)
            messages.append(msg)
            context_so_far.append({
                "agent": agent.name,
                "verdict": msg.verdict,
                "confidence": msg.confidence,
                "reasoning": msg.reasoning,
            })

        # FundManager synthesises
        fm_agent = self._get_agent("FundManager")
        synthesis_data = dict(signal_data)
        synthesis_data["all_opinions"] = context_so_far
        synthesis = self._agent_analyse(fm_agent, synthesis_data, round_num=3)

        vote_result = self._vote(messages)

        return {
            "final_verdict": synthesis.verdict,
            "confidence": synthesis.confidence,
            "round_1": [m.to_dict() for m in messages],
            "round_2": [],
            "round_3": synthesis.to_dict(),
            "vote_breakdown": vote_result,
            "red_flags": vote_result.get("red_flags", []),
        }

    def _run_adversarial(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """ADVERSARIAL protocol: 3-round bull vs bear debate."""
        # Round 1: Independent analysis
        round1 = self._round_1_analysis(signal_data)

        # Round 2: Challenge phase
        round2 = self._round_2_challenge(round1, signal_data)

        # Round 3: FundManager synthesis
        round3 = self._round_3_synthesis(round1, round2, signal_data)

        # Final vote combines all rounds
        all_messages = round1 + round2
        vote_result = self._vote(all_messages)

        # FundManager can override the vote
        final_verdict = round3.get("verdict", vote_result["verdict"])
        final_confidence = round3.get("confidence", vote_result["confidence"])

        # Aggregate red flags
        all_flags: List[str] = []
        for msg in round1 + round2:
            for flag in msg.red_flags:
                tagged = f"[{msg.agent_name}] {flag}"
                if tagged not in all_flags:
                    all_flags.append(tagged)

        return {
            "final_verdict": final_verdict,
            "confidence": final_confidence,
            "round_1": [m.to_dict() for m in round1],
            "round_2": [m.to_dict() for m in round2],
            "round_3": round3,
            "vote_breakdown": vote_result,
            "red_flags": all_flags,
        }

    # ── Round Implementations ────────────────────────────────────────

    def _round_1_analysis(
        self, signal_data: Dict[str, Any]
    ) -> List[DebateMessage]:
        """Round 1: Each agent independently analyses the signal.

        Args:
            signal_data: Raw signal data.

        Returns:
            List of DebateMessages from all non-FundManager agents.
        """
        messages: List[DebateMessage] = []

        for agent in self._agents:
            if agent.name == "FundManager":
                continue  # FundManager only participates in Round 3

            msg = self._agent_analyse(agent, signal_data, round_num=1)
            messages.append(msg)

        return messages

    def _round_2_challenge(
        self,
        round1_results: List[DebateMessage],
        signal_data: Dict[str, Any],
    ) -> List[DebateMessage]:
        """Round 2: Agents challenge each other's conclusions.

        Bull agents (BUY verdict) challenge bear agents (SELL/VETO)
        and vice versa. Each agent produces a revised opinion after
        considering the challenges.

        Args:
            round1_results: Messages from Round 1.
            signal_data: Original signal data.

        Returns:
            List of Round 2 challenge messages.
        """
        # Classify agents into bull and bear camps
        bulls: List[DebateMessage] = []
        bears: List[DebateMessage] = []
        neutrals: List[DebateMessage] = []

        for msg in round1_results:
            if msg.verdict == "BUY":
                bulls.append(msg)
            elif msg.verdict in ("SELL", "VETO"):
                bears.append(msg)
            else:
                neutrals.append(msg)

        challenges: List[DebateMessage] = []

        # Bulls challenge bears
        for bull_msg in bulls:
            bear_points = [
                f"{m.agent_name}: {m.reasoning}" for m in bears
            ]
            if not bear_points:
                continue

            agent = self._get_agent(bull_msg.agent_name)
            challenge_data = dict(signal_data)
            challenge_data["my_round1_verdict"] = bull_msg.verdict
            challenge_data["my_round1_reasoning"] = bull_msg.reasoning
            challenge_data["opposing_arguments"] = bear_points

            challenge_msg = self._agent_challenge(agent, challenge_data)
            challenges.append(challenge_msg)

        # Bears challenge bulls
        for bear_msg in bears:
            bull_points = [
                f"{m.agent_name}: {m.reasoning}" for m in bulls
            ]
            if not bull_points:
                continue

            agent = self._get_agent(bear_msg.agent_name)
            challenge_data = dict(signal_data)
            challenge_data["my_round1_verdict"] = bear_msg.verdict
            challenge_data["my_round1_reasoning"] = bear_msg.reasoning
            challenge_data["opposing_arguments"] = bull_points

            challenge_msg = self._agent_challenge(agent, challenge_data)
            challenges.append(challenge_msg)

        return challenges

    def _round_3_synthesis(
        self,
        round1: List[DebateMessage],
        round2: List[DebateMessage],
        signal_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Round 3: FundManager synthesises all evidence.

        The FundManager reviews all Round 1 analyses and Round 2
        challenges, then produces the final portfolio-level decision.

        Args:
            round1: Round 1 messages.
            round2: Round 2 challenge messages.
            signal_data: Original signal data.

        Returns:
            Synthesis dict with verdict, confidence, reasoning.
        """
        fm_agent = self._get_agent("FundManager")

        # Prepare synthesis context
        r1_summary = []
        for msg in round1:
            r1_summary.append({
                "agent": msg.agent_name,
                "verdict": msg.verdict,
                "confidence": msg.confidence,
                "reasoning": msg.reasoning,
                "red_flags": msg.red_flags,
            })

        r2_summary = []
        for msg in round2:
            r2_summary.append({
                "agent": msg.agent_name,
                "verdict": msg.verdict,
                "confidence": msg.confidence,
                "reasoning": msg.reasoning,
            })

        synthesis_data = dict(signal_data)
        synthesis_data["round1_opinions"] = r1_summary
        synthesis_data["round2_challenges"] = r2_summary

        # FundManager analysis
        fm_msg = self._agent_analyse(fm_agent, synthesis_data, round_num=3)

        # Weighted vote from Round 1 for context
        vote = self._vote(round1)

        # FundManager can override but is influenced by the vote
        # If strong consensus, FundManager follows it
        # If divided, FundManager exercises judgement
        consensus_strength = abs(
            vote.get("buy_weight", 0) - vote.get("sell_weight", 0)
        )

        if consensus_strength > 0.3:
            # Strong consensus — follow the vote
            final_verdict = vote["verdict"]
            final_confidence = vote["confidence"]
        else:
            # Divided — FundManager decides
            final_verdict = fm_msg.verdict
            final_confidence = fm_msg.confidence * 0.8  # Slight discount for uncertainty

        return {
            "verdict": final_verdict,
            "confidence": round(final_confidence, 4),
            "reasoning": fm_msg.reasoning,
            "consensus_strength": round(consensus_strength, 4),
            "fund_manager_verdict": fm_msg.verdict,
            "fund_manager_confidence": fm_msg.confidence,
            "vote_verdict": vote["verdict"],
            "red_flags": fm_msg.red_flags,
        }

    # ── Agent Analysis (Rule-Based Heuristics) ────────────────────

    def _agent_analyse(
        self,
        agent: DebateAgent,
        signal_data: Dict[str, Any],
        round_num: int = 1,
    ) -> DebateMessage:
        """Produce an analysis message from an agent.

        Uses rule-based heuristics. Can be extended to use AI via
        claude_fn/gemini_fn callbacks.

        Args:
            agent: The agent configuration.
            signal_data: Signal + context data.
            round_num: Which debate round.

        Returns:
            DebateMessage with the agent's analysis.
        """
        try:
            # Dispatch to the appropriate rule-based analyser
            analyser = self._get_analyser(agent.name)
            return analyser(agent, signal_data, round_num)
        except Exception as e:
            log.error("Agent %s analysis failed: %s", agent.name, e)
            return DebateMessage(
                agent_name=agent.name,
                round_num=round_num,
                verdict="HOLD",
                confidence=0.1,
                reasoning=f"Agent error: {e}",
            )

    def _agent_challenge(
        self,
        agent: DebateAgent,
        challenge_data: Dict[str, Any],
    ) -> DebateMessage:
        """Produce a challenge message in Round 2.

        The agent reviews opposing arguments and either:
        - Maintains its position (with counter-arguments)
        - Revises its verdict (if opposing evidence is compelling)

        Args:
            agent: The challenging agent.
            challenge_data: Includes my_round1_verdict, opposing_arguments.

        Returns:
            DebateMessage with revised verdict.
        """
        my_verdict = challenge_data.get("my_round1_verdict", "HOLD")
        opposing = challenge_data.get("opposing_arguments", [])
        n_opposing = len(opposing)

        # Simple heuristic: more opposing arguments = more likely to revise
        revision_probability = min(0.3, n_opposing * 0.08)

        confidence_adjustment = -0.05 * n_opposing  # Each opposing view reduces confidence
        original_confidence = challenge_data.get("confidence", 0.5)
        new_confidence = max(0.1, original_confidence + confidence_adjustment)

        # Detect strong opposing consensus
        if n_opposing >= 4:
            # Many opponents — likely revise
            revision_probability = 0.5

        # Decide whether to revise
        should_revise = np.random.random() < revision_probability

        if should_revise and my_verdict == "BUY":
            new_verdict = "HOLD"
            reasoning = (f"{agent.name} revised BUY to HOLD after reviewing "
                         f"{n_opposing} opposing arguments")
        elif should_revise and my_verdict in ("SELL", "VETO"):
            new_verdict = "HOLD"
            reasoning = (f"{agent.name} revised {my_verdict} to HOLD after "
                         f"reviewing {n_opposing} counter-arguments")
        else:
            new_verdict = my_verdict
            reasoning = (f"{agent.name} maintains {my_verdict} despite "
                         f"{n_opposing} opposing views")

        return DebateMessage(
            agent_name=agent.name,
            round_num=2,
            verdict=new_verdict,
            confidence=round(new_confidence, 3),
            reasoning=reasoning,
            red_flags=[],
        )

    # ── Rule-Based Analysers ─────────────────────────────────────────

    def _get_analyser(
        self, agent_name: str
    ) -> Callable[[DebateAgent, Dict[str, Any], int], DebateMessage]:
        """Get the rule-based analyser function for an agent."""
        analysers: Dict[str, Callable] = {
            "Fundamentals": self._analyse_fundamentals,
            "Sentiment": self._analyse_sentiment,
            "Technical": self._analyse_technical,
            "Microstructure": self._analyse_microstructure,
            "Regime": self._analyse_regime,
            "Risk": self._analyse_risk,
            "Researcher": self._analyse_researcher,
            "Trader": self._analyse_trader,
            "FundManager": self._analyse_fund_manager,
        }
        return analysers.get(agent_name, self._analyse_default)

    def _analyse_fundamentals(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Fundamentals agent: valuation, earnings, sector rotation."""
        direction = data.get("direction", "long")
        pe_ratio = data.get("pe_ratio", 0.0)
        earnings_surprise = data.get("earnings_surprise", 0.0)
        sector_momentum = data.get("sector_momentum", 0.0)

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        if pe_ratio > 40:
            score -= 0.15
            flags.append(f"High P/E ({pe_ratio:.0f})")
        elif 0 < pe_ratio < 15:
            score += 0.10
            reasons.append(f"Attractive valuation (P/E={pe_ratio:.0f})")

        if earnings_surprise > 5:
            score += 0.15
            reasons.append(f"Positive earnings surprise ({earnings_surprise:.1f}%)")
        elif earnings_surprise < -5:
            score -= 0.15
            flags.append(f"Negative earnings surprise ({earnings_surprise:.1f}%)")

        if sector_momentum > 0.5:
            score += 0.10
            reasons.append("Strong sector momentum")
        elif sector_momentum < -0.5:
            score -= 0.10
            flags.append("Weak sector momentum")

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=round(abs(score - 0.5) * 2, 3),
            reasoning="; ".join(reasons) or "No strong fundamental signal",
            red_flags=flags,
        )

    def _analyse_sentiment(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Sentiment agent: news, social, positioning."""
        news_score = data.get("news_sentiment", 0.0)  # -1 to 1
        social_score = data.get("social_sentiment", 0.0)
        put_call_ratio = data.get("put_call_ratio", 1.0)
        short_interest = data.get("short_interest_pct", 0.0)
        direction = data.get("direction", "long")

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        if news_score > 0.3:
            score += 0.15
            reasons.append(f"Positive news sentiment ({news_score:.2f})")
        elif news_score < -0.3:
            score -= 0.15
            flags.append(f"Negative news sentiment ({news_score:.2f})")

        if social_score > 0.5:
            score += 0.10
            reasons.append("Strong positive social sentiment")
        elif social_score < -0.5:
            score -= 0.10

        # Contrarian: extreme put/call is contrarian bullish
        if put_call_ratio > 1.5:
            score += 0.08 if direction == "long" else -0.08
            reasons.append(f"High put/call ratio ({put_call_ratio:.2f}) — contrarian bullish")

        if short_interest > 15:
            score += 0.05 if direction == "long" else -0.05
            reasons.append(f"High short interest ({short_interest:.1f}%) — squeeze potential")

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=round(min(abs(score - 0.5) * 2, 0.7), 3),
            reasoning="; ".join(reasons) or "Neutral sentiment",
            red_flags=flags,
        )

    def _analyse_technical(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Technical agent: price action, indicators, patterns."""
        rsi = data.get("rsi", 50.0)
        macd_signal = data.get("macd_signal", 0.0)  # Positive = bullish
        above_sma200 = data.get("above_sma200", True)
        atr_pct = data.get("atr_pct", 2.0)
        direction = data.get("direction", "long")
        confidence_raw = data.get("confidence", 50)

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        # RSI analysis
        if direction == "long":
            if rsi > 75:
                score -= 0.15
                flags.append(f"Overbought RSI ({rsi:.0f})")
            elif rsi < 35:
                score += 0.15
                reasons.append(f"Oversold RSI ({rsi:.0f}) — rebound potential")
            elif 40 <= rsi <= 60:
                score += 0.05
                reasons.append("RSI in neutral zone")
        else:
            if rsi < 25:
                score -= 0.15
                flags.append(f"Oversold RSI ({rsi:.0f})")
            elif rsi > 65:
                score += 0.15

        # MACD
        if macd_signal > 0 and direction == "long":
            score += 0.10
            reasons.append("MACD bullish crossover")
        elif macd_signal < 0 and direction == "long":
            score -= 0.10
            flags.append("MACD bearish")

        # Trend filter
        if above_sma200 and direction == "long":
            score += 0.10
            reasons.append("Above 200-day SMA (uptrend)")
        elif not above_sma200 and direction == "long":
            score -= 0.10
            flags.append("Below 200-day SMA")

        # ATR volatility
        if atr_pct > 5.0:
            score -= 0.05
            flags.append(f"High ATR ({atr_pct:.1f}%) — volatile")

        # Signal confidence
        if confidence_raw >= 70:
            score += 0.05
        elif confidence_raw < 55:
            score -= 0.05

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=round(min(abs(score - 0.5) * 2, 0.85), 3),
            reasoning="; ".join(reasons) or "Neutral technicals",
            red_flags=flags,
        )

    def _analyse_microstructure(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Microstructure agent: order book, spreads, tick data."""
        spread_pct = data.get("spread_pct", 0.10)
        rvol = data.get("rvol", 1.0)
        bid_ask_imbalance = data.get("bid_ask_imbalance", 0.0)  # Positive = more bids
        tick_direction = data.get("tick_direction", 0)  # +1 uptick, -1 downtick
        direction = data.get("direction", "long")

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        # Spread analysis
        if spread_pct > 0.30:
            score -= 0.20
            flags.append(f"Wide spread ({spread_pct:.2f}%) — poor liquidity")
        elif spread_pct < 0.08:
            score += 0.15
            reasons.append(f"Tight spread ({spread_pct:.2f}%)")
        elif spread_pct < 0.15:
            score += 0.05

        # Volume
        if rvol > 1.5:
            score += 0.10
            reasons.append(f"Above-average volume ({rvol:.1f}x)")
        elif rvol < 0.5:
            score -= 0.15
            flags.append(f"Low volume ({rvol:.1f}x) — thin market")

        # Order book imbalance
        if direction == "long" and bid_ask_imbalance > 0.2:
            score += 0.10
            reasons.append("Bid-heavy order book")
        elif direction == "long" and bid_ask_imbalance < -0.2:
            score -= 0.10
            flags.append("Ask-heavy order book (selling pressure)")

        # Tick direction
        if direction == "long" and tick_direction > 0:
            score += 0.05
            reasons.append("Positive tick direction")
        elif direction == "long" and tick_direction < 0:
            score -= 0.05

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=round(abs(score - 0.5) * 2, 3),
            reasoning="; ".join(reasons) or "Neutral microstructure",
            red_flags=flags,
        )

    def _analyse_regime(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Regime agent: macro, volatility, correlation regime."""
        regime = data.get("regime", "unknown")
        vix = data.get("vix", 20.0)
        hurst = data.get("hurst", 0.5)
        hmm_state = data.get("hmm_state", -1)
        direction = data.get("direction", "long")
        strategy = data.get("strategy", "")

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        # Regime-strategy compatibility (same logic as Book 62)
        trend_strategies = {"VanguardSniper", "S3_MacroTrend", "S5_OvernightCarry"}
        reversion_strategies = {"S2_Reversion", "ApexScout"}

        if regime in ("trending", "trend_up", "trend_down"):
            if strategy in trend_strategies:
                score += 0.20
                reasons.append("Trend strategy compatible with trending regime")
            elif strategy in reversion_strategies:
                score -= 0.15
                flags.append("Reversion strategy in trending regime")

        elif regime in ("mean_reverting", "range", "choppy"):
            if strategy in reversion_strategies:
                score += 0.20
                reasons.append("Reversion strategy compatible with ranging regime")
            elif strategy in trend_strategies:
                score -= 0.15
                flags.append("Trend strategy in choppy regime")

        elif regime in ("volatile", "crisis"):
            score -= 0.15
            flags.append(f"Crisis/volatile regime — elevated risk")

        # VIX
        if vix > 30:
            score -= 0.10
            flags.append(f"VIX elevated ({vix:.1f})")
        elif vix < 15:
            score += 0.05

        # Hurst
        if hurst > 0.6:
            reasons.append(f"Hurst={hurst:.2f} suggests trend persistence")
            if strategy in trend_strategies:
                score += 0.05
        elif hurst < 0.4:
            reasons.append(f"Hurst={hurst:.2f} suggests mean-reversion")
            if strategy in reversion_strategies:
                score += 0.05

        # HMM
        if hmm_state == 1 and direction == "long":
            score -= 0.10
            flags.append("HMM bearish state vs long signal")
        elif hmm_state == 0 and direction == "long":
            score += 0.05

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=0.65 if regime != "unknown" else 0.3,
            reasoning="; ".join(reasons) or "No strong regime signal",
            red_flags=flags,
        )

    def _analyse_risk(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Risk agent: position risk, portfolio impact, tail risk."""
        kelly = data.get("kelly", 0.10)
        n_positions = data.get("n_positions", 0)
        max_positions = data.get("max_positions", 10)
        drawdown_pct = data.get("drawdown_pct", 0.0)
        max_dd_limit = data.get("max_drawdown_pct", 5.0)
        daily_loss_pct = data.get("daily_loss_pct", 0.0)
        ticker = data.get("ticker", "")
        open_tickers = data.get("open_tickers", [])
        direction = data.get("direction", "long")

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        # Position limits
        if n_positions >= max_positions:
            score -= 0.30
            flags.append(f"At max positions ({n_positions}/{max_positions})")
        elif n_positions >= max_positions * 0.8:
            score -= 0.10
        else:
            score += 0.10
            reasons.append(f"Position capacity OK ({n_positions}/{max_positions})")

        # Drawdown
        if drawdown_pct > max_dd_limit * 0.8:
            score -= 0.25
            flags.append(f"Near drawdown limit ({drawdown_pct:.1f}%)")
        elif drawdown_pct > max_dd_limit * 0.5:
            score -= 0.10

        # Daily loss
        if daily_loss_pct > 1.5:
            score -= 0.20
            flags.append(f"Heavy daily loss ({daily_loss_pct:.1f}%)")

        # Duplicate position
        if ticker in open_tickers:
            score -= 0.15
            flags.append(f"Duplicate position in {ticker}")

        # Kelly sanity
        if kelly > 0.30:
            flags.append(f"Aggressive Kelly ({kelly:.2f})")
            score -= 0.05
        elif kelly < 0.05:
            reasons.append("Conservative sizing")
            score += 0.05

        score = float(np.clip(score, 0.0, 1.0))

        # Risk agent can VETO if score is very low
        if score < 0.25:
            verdict = "VETO"
        else:
            verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=0.75,
            reasoning="; ".join(reasons) or "Standard risk assessment",
            red_flags=flags,
        )

    def _analyse_researcher(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Researcher agent: statistical edge, decay, academic rigour."""
        n_trades = data.get("strategy_n_trades", 0)
        win_rate = data.get("strategy_win_rate", 0.5)
        profit_factor = data.get("strategy_profit_factor", 1.0)
        edge_zscore = data.get("edge_zscore", 0.0)
        alpha_decay_days = data.get("alpha_decay_days", 0)
        direction = data.get("direction", "long")

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        # Sample size
        if n_trades >= 50:
            score += 0.10
            reasons.append(f"Adequate sample ({n_trades} trades)")
        elif n_trades >= 20:
            score += 0.03
        elif n_trades < 10:
            score -= 0.15
            flags.append(f"Insufficient sample ({n_trades} trades)")

        # Statistical significance
        if edge_zscore > 2.5:
            score += 0.20
            reasons.append(f"Highly significant edge (z={edge_zscore:.1f})")
        elif edge_zscore > 2.0:
            score += 0.10
            reasons.append(f"Significant edge (z={edge_zscore:.1f})")
        elif edge_zscore < 1.0 and n_trades > 30:
            score -= 0.15
            flags.append(f"Edge not significant (z={edge_zscore:.1f})")

        # Profit factor
        if profit_factor > 1.5 and n_trades >= 20:
            score += 0.10
            reasons.append(f"Strong profit factor ({profit_factor:.2f})")
        elif profit_factor < 1.0 and n_trades >= 20:
            score -= 0.15
            flags.append(f"Unprofitable (PF={profit_factor:.2f})")

        # Alpha decay
        if alpha_decay_days > 60:
            score -= 0.10
            flags.append(f"Alpha decay detected ({alpha_decay_days}d)")
        elif alpha_decay_days > 30:
            score -= 0.05
            reasons.append(f"Possible alpha decay ({alpha_decay_days}d)")

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=round(min(0.3 + n_trades / 100.0, 0.8), 3),
            reasoning="; ".join(reasons) or "Insufficient statistical evidence",
            red_flags=flags,
        )

    def _analyse_trader(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Trader agent: execution quality, timing, slippage."""
        spread_pct = data.get("spread_pct", 0.10)
        session = data.get("session", "unknown")
        rvol = data.get("rvol", 1.0)
        expected_edge_pct = data.get("expected_edge_pct", 0.30)
        holding_period = data.get("holding_period_bars", 50)
        direction = data.get("direction", "long")

        score = 0.5
        flags: List[str] = []
        reasons: List[str] = []

        # Cost-to-edge ratio
        total_cost_pct = spread_pct * 2  # Simplified round-trip
        if expected_edge_pct > 0:
            cost_edge = total_cost_pct / expected_edge_pct
            if cost_edge > 0.5:
                score -= 0.20
                flags.append(f"Costs consume {cost_edge:.0%} of edge")
            elif cost_edge < 0.2:
                score += 0.15
                reasons.append(f"Good cost-to-edge ratio ({cost_edge:.0%})")

        # Session timing
        good_sessions = {"us_morning", "london_morning", "overlap"}
        bad_sessions = {"pre_market", "after_hours", "lunch"}
        if session in good_sessions:
            score += 0.10
            reasons.append(f"Good session ({session})")
        elif session in bad_sessions:
            score -= 0.10
            flags.append(f"Poor session ({session})")

        # Volume
        if rvol > 1.5:
            score += 0.05
            reasons.append("Good liquidity")
        elif rvol < 0.5:
            score -= 0.10
            flags.append("Thin market — slippage risk")

        # Holding period vs costs
        if holding_period < 10 and spread_pct > 0.15:
            score -= 0.15
            flags.append("Short hold + wide spread = cost drag")

        score = float(np.clip(score, 0.0, 1.0))
        verdict = self._score_to_verdict(score, direction)

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict, confidence=round(abs(score - 0.5) * 2, 3),
            reasoning="; ".join(reasons) or "Neutral execution outlook",
            red_flags=flags,
        )

    def _analyse_fund_manager(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """FundManager agent: final synthesis and portfolio decision."""
        opinions = data.get("round1_opinions", data.get("all_opinions", []))
        challenges = data.get("round2_challenges", [])

        if not opinions:
            return DebateMessage(
                agent_name=agent.name, round_num=round_num,
                verdict="HOLD", confidence=0.3,
                reasoning="No agent opinions to synthesise",
            )

        # Count verdicts
        buy_count = sum(1 for o in opinions if o.get("verdict") == "BUY")
        sell_count = sum(1 for o in opinions if o.get("verdict") in ("SELL", "VETO"))
        hold_count = sum(1 for o in opinions if o.get("verdict") == "HOLD")
        veto_count = sum(1 for o in opinions if o.get("verdict") == "VETO")
        total = len(opinions)

        # Aggregate red flags
        all_flags: List[str] = []
        for o in opinions:
            for f in o.get("red_flags", []):
                if f not in all_flags:
                    all_flags.append(f)

        # Average confidence
        avg_conf = float(np.mean([o.get("confidence", 0.5) for o in opinions]))

        reasons: List[str] = []

        # Decision logic
        if veto_count >= 2:
            verdict = "VETO"
            confidence = 0.85
            reasons.append(f"{veto_count} agents issued VETO")
        elif len(all_flags) >= 6:
            verdict = "VETO"
            confidence = 0.80
            reasons.append(f"{len(all_flags)} red flags — too many concerns")
        elif buy_count >= total * 0.6:
            verdict = "BUY"
            confidence = avg_conf * 0.9
            reasons.append(f"Strong buy consensus ({buy_count}/{total})")
        elif sell_count >= total * 0.5:
            verdict = "SELL"
            confidence = avg_conf * 0.85
            reasons.append(f"Sell/veto majority ({sell_count}/{total})")
        elif buy_count > sell_count:
            verdict = "BUY"
            confidence = avg_conf * 0.7
            reasons.append(f"Lean buy ({buy_count} vs {sell_count})")
        elif sell_count > buy_count:
            verdict = "SELL"
            confidence = avg_conf * 0.7
            reasons.append(f"Lean sell ({sell_count} vs {buy_count})")
        else:
            verdict = "HOLD"
            confidence = 0.4
            reasons.append(f"No consensus ({buy_count}B/{sell_count}S/{hold_count}H)")

        # Challenge round impact
        if challenges:
            n_revisions = sum(
                1 for c in challenges
                if c.get("verdict") != data.get("my_round1_verdict")
            )
            if n_revisions > 0:
                reasons.append(f"{n_revisions} agents revised after challenges")

        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict=verdict,
            confidence=round(min(confidence, 0.95), 3),
            reasoning="; ".join(reasons),
            red_flags=all_flags[:5],  # Top 5 flags
        )

    def _analyse_default(
        self, agent: DebateAgent, data: Dict[str, Any], round_num: int
    ) -> DebateMessage:
        """Default fallback analyser."""
        return DebateMessage(
            agent_name=agent.name, round_num=round_num,
            verdict="HOLD", confidence=0.3,
            reasoning=f"No specific analyser for {agent.name}",
        )

    # ── Voting & Scoring ─────────────────────────────────────────────

    def _score_to_verdict(self, score: float, direction: str) -> str:
        """Convert a numeric score to a verdict.

        Args:
            score: 0.0 (very bearish) to 1.0 (very bullish).
            direction: Signal direction ("long" or "short").

        Returns:
            "BUY", "SELL", "HOLD", or "VETO".
        """
        if direction == "long":
            if score >= 0.65:
                return "BUY"
            elif score <= 0.30:
                return "SELL"
            else:
                return "HOLD"
        else:  # short
            if score >= 0.65:
                return "SELL"
            elif score <= 0.30:
                return "BUY"
            else:
                return "HOLD"

    def _vote(self, messages: List[DebateMessage]) -> Dict[str, Any]:
        """Compute weighted vote from agent messages.

        Uses agent base weight * credibility * confidence for
        effective weight.

        Args:
            messages: List of DebateMessages to vote on.

        Returns:
            Dict with verdict, confidence, and breakdown.
        """
        buy_weight = 0.0
        sell_weight = 0.0
        hold_weight = 0.0
        veto_count = 0
        total_weight = 0.0
        all_flags: List[str] = []

        for msg in messages:
            agent = self._get_agent(msg.agent_name)
            base_w = agent.weight
            cred = self._credibility.get(agent.name, 0.5)
            eff_w = base_w * cred * msg.confidence

            if msg.verdict == "BUY":
                buy_weight += eff_w
            elif msg.verdict == "SELL":
                sell_weight += eff_w
            elif msg.verdict == "VETO":
                veto_count += 1
                sell_weight += eff_w * 1.5  # VETO gets extra weight
            else:
                hold_weight += eff_w

            total_weight += eff_w

            for flag in msg.red_flags:
                tagged = f"[{msg.agent_name}] {flag}"
                if tagged not in all_flags:
                    all_flags.append(tagged)

        # Decision
        if veto_count >= 2:
            verdict = "VETO"
            confidence = 0.9
        elif total_weight > 0:
            buy_pct = buy_weight / total_weight
            sell_pct = sell_weight / total_weight

            if buy_pct > 0.55:
                verdict = "BUY"
                confidence = buy_pct
            elif sell_pct > 0.50:
                verdict = "SELL"
                confidence = sell_pct
            else:
                verdict = "HOLD"
                confidence = hold_weight / max(total_weight, 1e-10)
        else:
            verdict = "HOLD"
            confidence = 0.3

        # Penalty for many red flags
        if len(all_flags) >= 5:
            confidence *= 0.8
        if len(all_flags) >= 8:
            verdict = "VETO" if verdict in ("SELL", "HOLD") else verdict

        return {
            "verdict": verdict,
            "confidence": round(min(confidence, 0.95), 4),
            "buy_weight": round(buy_weight, 4),
            "sell_weight": round(sell_weight, 4),
            "hold_weight": round(hold_weight, 4),
            "veto_count": veto_count,
            "total_weight": round(total_weight, 4),
            "n_agents": len(messages),
            "red_flags": all_flags,
        }

    def _update_credibility(self, agent_name: str, was_correct: bool) -> None:
        """Update an agent's credibility based on trade outcome.

        Uses EMA: cred = alpha * outcome + (1 - alpha) * old_cred

        Args:
            agent_name: Agent to update.
            was_correct: Whether the agent's verdict aligned with the outcome.
        """
        alpha = 0.1  # EMA smoothing factor
        old = self._credibility.get(agent_name, 0.5)
        outcome = 1.0 if was_correct else 0.0
        new = alpha * outcome + (1.0 - alpha) * old
        self._credibility[agent_name] = round(new, 6)

        # Update the agent object too
        agent = self._get_agent(agent_name)
        agent.credibility = new

        self._save_credibility()
        log.debug("Credibility update: %s %.4f -> %.4f (correct=%s)",
                  agent_name, old, new, was_correct)

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_agent(self, name: str) -> DebateAgent:
        """Get agent by name."""
        for agent in self._agents:
            if agent.name == name:
                return agent
        # Fallback: create a default agent
        return DebateAgent(name=name, role="unknown", weight=0.05)

    # ── Persistence ──────────────────────────────────────────────────

    def _load_credibility(self) -> None:
        """Load credibility scores from JSON."""
        if not CREDIBILITY_PATH.exists():
            self._credibility = {a.name: 0.5 for a in self._agents}
            return

        try:
            with open(CREDIBILITY_PATH, "r") as f:
                data = json.load(f)
            # Support both old (Book 62) and new (Book 140) formats
            cred_data = data.get("credibility", data.get("credibility_9agent", {}))

            # Map agent names
            self._credibility = {}
            for agent in self._agents:
                self._credibility[agent.name] = cred_data.get(agent.name, 0.5)

            log.info("Loaded 9-agent credibility: %s", self._credibility)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load credibility: %s — using defaults", e)
            self._credibility = {a.name: 0.5 for a in self._agents}

    def _save_credibility(self) -> None:
        """Persist credibility scores to JSON."""
        try:
            CREDIBILITY_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Preserve existing data (e.g. Book 62 credibility)
            existing = {}
            if CREDIBILITY_PATH.exists():
                try:
                    with open(CREDIBILITY_PATH, "r") as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

            existing["credibility_9agent"] = self._credibility
            existing["updated_at_9agent"] = datetime.now(timezone.utc).isoformat()

            with open(CREDIBILITY_PATH, "w") as f:
                json.dump(existing, f, indent=2)
        except OSError as e:
            log.error("Failed to save credibility: %s", e)

    def _log_debate(self, result: Dict[str, Any]) -> None:
        """Append debate result to NDJSON log."""
        try:
            DEBATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(DEBATE_LOG_PATH, "a") as f:
                f.write(json.dumps(result, default=str) + "\n")
        except OSError as e:
            log.error("Failed to log debate: %s", e)


# Alias for bridge.py backward compatibility (some imports use Debate9).
Debate9 = NineAgentDebate
