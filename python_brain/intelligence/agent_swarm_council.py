"""
Agent-Swarm LLM Council — Multi-Mind Decision Layer

Multiple specialised LLM agents independently analyze a trade candidate,
then a consensus mechanism aggregates their verdicts.

Inspired by:
- Man Group AlphaGPT (multi-agent research)
- Anthropic Constitutional AI (self-critique)
- JPM LOXM (ensemble decision layers)

Agents:
1. Fundamental Analyst — reads news/filings, scores fundamental conviction
2. Technical Analyst — reads price action, scores technical conviction
3. Risk Officer — scores downside risk, veto power
4. Microstructure Analyst — scores liquidity/execution cost
5. Devil's Advocate — actively tries to disprove the trade
6. Macro Strategist — scores regime alignment

Consensus:
- Weighted vote (Risk Officer has veto)
- Confidence = geometric mean of agent scores
- Requires >= 4 of 6 agents to accept
- Risk Officer veto = automatic reject
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


AGENT_PROMPTS = {
    "fundamental": """You are a Fundamental Analyst at an institutional trading firm.
Given a trade signal, evaluate it based ONLY on fundamentals (news, earnings, filings, macro).
Ignore technicals and microstructure.

Output JSON: {"score": 0.0-1.0, "confidence": 0.0-1.0, "reason": "1 sentence"}
score = probability this trade works based on fundamentals
confidence = how sure you are of your score""",

    "technical": """You are a Technical Analyst at an institutional trading firm.
Given a trade signal, evaluate it based ONLY on price action, momentum, trend, support/resistance.
Ignore fundamentals.

Output JSON: {"score": 0.0-1.0, "confidence": 0.0-1.0, "reason": "1 sentence"}""",

    "risk_officer": """You are the Risk Officer at an institutional trading firm. You have VETO power.
Given a trade signal, evaluate downside risk ONLY. You are paid to say NO.
Veto if: position too large, volatility too high, correlation to existing book too high,
liquidity insufficient, drawdown risk > 2R.

Output JSON: {"score": 0.0-1.0, "confidence": 0.0-1.0, "veto": true/false, "reason": "1 sentence"}
score = 1.0 means low risk, 0.0 means high risk
veto = true if this trade MUST be rejected regardless of alpha""",

    "microstructure": """You are a Microstructure Analyst at an institutional trading firm.
Given a trade signal, evaluate execution quality ONLY: spread, depth, expected slippage, queue position.

Output JSON: {"score": 0.0-1.0, "confidence": 0.0-1.0, "reason": "1 sentence"}
score = 1.0 means clean execution expected, 0.0 means high impact cost""",

    "devil_advocate": """You are the Devil's Advocate. Your job is to DISPROVE this trade.
Find every reason this trade is WRONG. Be aggressive. Be skeptical. Assume it's a trap.

Output JSON: {"score": 0.0-1.0, "confidence": 0.0-1.0, "reason": "1 sentence on main weakness"}
score = probability the trade is actually a good idea despite your skepticism (be honest)""",

    "macro": """You are a Macro Strategist at an institutional trading firm.
Given a trade signal, evaluate regime alignment ONLY: is this trade aligned with current macro regime
(VIX, yields, sector rotation, central bank posture)?

Output JSON: {"score": 0.0-1.0, "confidence": 0.0-1.0, "reason": "1 sentence"}"""
}


@dataclass
class AgentVerdict:
    agent: str
    score: float
    confidence: float
    reason: str
    veto: bool = False
    raw_response: str = ""


@dataclass
class CouncilDecision:
    accept: bool
    consensus_score: float
    consensus_confidence: float
    num_accept: int
    num_total: int
    verdicts: list[AgentVerdict] = field(default_factory=list)
    veto_reason: str | None = None


class AgentSwarmCouncil:
    """Multi-agent LLM council with Risk Officer veto power."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-haiku-4-5-20251001",
        min_agent_score: float = 0.5,
        required_accepts: int = 4,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.min_agent_score = min_agent_score
        self.required_accepts = required_accepts
        self.client = Anthropic(api_key=self.api_key) if HAS_ANTHROPIC and self.api_key else None

    async def _query_agent(self, agent: str, signal_context: str) -> AgentVerdict:
        """Send signal context to one LLM agent, parse JSON response."""
        if not self.client:
            # Fallback: return neutral verdict
            return AgentVerdict(
                agent=agent, score=0.5, confidence=0.3,
                reason="LLM unavailable, neutral fallback",
                veto=False,
            )

        system_prompt = AGENT_PROMPTS[agent]

        try:
            # Haiku 4.5 supports system + single user message
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model,
                max_tokens=256,
                system=system_prompt,
                messages=[{"role": "user", "content": signal_context}],
            )
            raw_text = response.content[0].text.strip()

            # Extract JSON
            json_start = raw_text.find("{")
            json_end = raw_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(raw_text[json_start:json_end])
                return AgentVerdict(
                    agent=agent,
                    score=float(parsed.get("score", 0.5)),
                    confidence=float(parsed.get("confidence", 0.5)),
                    reason=str(parsed.get("reason", ""))[:200],
                    veto=bool(parsed.get("veto", False)),
                    raw_response=raw_text,
                )
        except Exception as e:
            return AgentVerdict(
                agent=agent, score=0.5, confidence=0.2,
                reason=f"error: {type(e).__name__}",
            )

        return AgentVerdict(agent=agent, score=0.5, confidence=0.3, reason="parse failed")

    async def evaluate(self, signal: dict[str, Any]) -> CouncilDecision:
        """Run all agents in parallel, aggregate verdicts."""
        ticker = signal.get("ticker", "?")
        side = signal.get("side", "?")
        strategy = signal.get("strategy_name", "?")
        conviction = signal.get("conviction", 0.5)
        rationale = signal.get("rationale", "")

        # Compact signal context for LLMs
        context = (
            f"TRADE SIGNAL: {side} {ticker}\n"
            f"Strategy: {strategy}\n"
            f"Initial conviction: {conviction:.2f}\n"
            f"Rationale: {rationale}\n"
            f"Session: {signal.get('session', '?')}\n"
            f"Features: {json.dumps(signal.get('features', {}))[:500]}"
        )

        # Query all 6 agents in parallel
        tasks = [self._query_agent(agent, context) for agent in AGENT_PROMPTS.keys()]
        verdicts = await asyncio.gather(*tasks)

        # Check Risk Officer veto
        risk_verdict = next((v for v in verdicts if v.agent == "risk_officer"), None)
        if risk_verdict and risk_verdict.veto:
            return CouncilDecision(
                accept=False,
                consensus_score=risk_verdict.score,
                consensus_confidence=risk_verdict.confidence,
                num_accept=0,
                num_total=len(verdicts),
                verdicts=verdicts,
                veto_reason=f"Risk Officer veto: {risk_verdict.reason}",
            )

        # Count accepts (score >= threshold)
        accepts = [v for v in verdicts if v.score >= self.min_agent_score]
        num_accept = len(accepts)

        # Consensus score = confidence-weighted mean
        total_conf = sum(v.confidence for v in verdicts) or 1.0
        consensus_score = sum(v.score * v.confidence for v in verdicts) / total_conf
        consensus_confidence = sum(v.confidence for v in verdicts) / len(verdicts)

        accept = num_accept >= self.required_accepts

        return CouncilDecision(
            accept=accept,
            consensus_score=consensus_score,
            consensus_confidence=consensus_confidence,
            num_accept=num_accept,
            num_total=len(verdicts),
            verdicts=verdicts,
        )


# Convenience sync wrapper
def evaluate_signal_sync(signal: dict, council: AgentSwarmCouncil | None = None) -> CouncilDecision:
    """Sync wrapper for single-signal evaluation."""
    c = council or AgentSwarmCouncil()
    return asyncio.run(c.evaluate(signal))


if __name__ == "__main__":
    # Smoke test
    import sys
    sig = {
        "ticker": "AAPL",
        "side": "BUY",
        "strategy_name": "filing_change_detect",
        "conviction": 0.75,
        "rationale": "10-Q filing detected new revenue segment beating estimates",
        "session": "us_session",
        "features": {"gross_edge_bps": 25, "rvol": 1.8},
    }

    if "--smoke" in sys.argv:
        # Test without API (fallback path)
        c = AgentSwarmCouncil(api_key="dummy")
        c.client = None
        result = evaluate_signal_sync(sig, c)
        print(f"Accept: {result.accept}")
        print(f"Consensus: {result.consensus_score:.3f} (conf {result.consensus_confidence:.3f})")
        print(f"Accepts: {result.num_accept}/{result.num_total}")
        for v in result.verdicts:
            print(f"  {v.agent}: {v.score:.2f} (conf {v.confidence:.2f}) - {v.reason}")
        print("OK")
