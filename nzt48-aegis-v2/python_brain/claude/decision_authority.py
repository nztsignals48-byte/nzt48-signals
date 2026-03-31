"""Claude Cold-Path Decision Authority — Books 72, 142, 198, 205, 210.

Defines the decision authority matrix for Claude's role in the system.
Claude operates on the COLD PATH (nightly, not real-time):
- Analyzes trades after the fact (forensic review)
- Challenges parameter changes (skeptic role)
- Proposes universe changes (curator role)
- NEVER directly executes trades

Authority levels (L0→L4) gate what Claude can do:
  L0 (N<100):  Read-only analysis, generate reports
  L1 (N>=100): Propose parameter changes (human approves)
  L2 (N>=300): Auto-adjust within ±10% of current params (2h veto window)
  L3 (N>=500): Full parameter authority within bounds (1h veto)
  L4 (N>=1000): Universe curation + strategy kill/promote (human confirms kills)

Decision types (each has a prompt template + output schema):
  D-NIGHTLY:    Post-session forensic review
  D-REGIME:     Regime classification challenge
  D-HYPOTHESIS: New strategy hypothesis generation
  D-ERROR:      Error/anomaly investigation
  D-PARAM:      Parameter change proposal
  D-UNIVERSE:   Universe expansion/contraction
  D-JOURNAL:    Institutional memory update

Usage:
    from python_brain.claude.decision_authority import (
        DecisionAuthority, DecisionType, AuthorityLevel,
    )

    authority = DecisionAuthority(trade_count=150)
    level = authority.current_level  # AuthorityLevel.L1

    can_adjust = authority.can_make_decision(DecisionType.PARAM_CHANGE)
    prompt = authority.get_prompt(DecisionType.NIGHTLY_REVIEW, context={...})
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("claude_authority")


class AuthorityLevel(Enum):
    L0 = 0  # Read-only analysis
    L1 = 1  # Propose changes (human approves)
    L2 = 2  # Auto-adjust ±10% (2h veto window)
    L3 = 3  # Full parameter authority within bounds (1h veto)
    L4 = 4  # Universe + strategy lifecycle authority


class DecisionType(Enum):
    NIGHTLY_REVIEW = "D-NIGHTLY"
    REGIME_CHALLENGE = "D-REGIME"
    HYPOTHESIS = "D-HYPOTHESIS"
    ERROR_INVESTIGATION = "D-ERROR"
    PARAM_CHANGE = "D-PARAM"
    UNIVERSE_CURATION = "D-UNIVERSE"
    JOURNAL_UPDATE = "D-JOURNAL"
    STRATEGY_KILL = "D-KILL"
    # Book 72: Additional decision types (all wired)
    FORENSIC = "D-FORENSIC"
    CLUSTER_ANALYSIS = "D-CLUSTER"
    CONFIG_AUDIT = "D-CONFIG"
    EDGE_DECAY = "D-DECAY"
    DEPLOY_REVIEW = "D-DEPLOY"


# Authority level required for each decision type
# FULLY AUTONOMOUS: All set to L0 — hard bounds are the safety net, not authority gates.
DECISION_AUTHORITY: Dict[DecisionType, AuthorityLevel] = {
    DecisionType.NIGHTLY_REVIEW: AuthorityLevel.L0,
    DecisionType.REGIME_CHALLENGE: AuthorityLevel.L0,
    DecisionType.ERROR_INVESTIGATION: AuthorityLevel.L0,
    DecisionType.JOURNAL_UPDATE: AuthorityLevel.L0,
    DecisionType.HYPOTHESIS: AuthorityLevel.L0,       # Was L1, now autonomous
    DecisionType.PARAM_CHANGE: AuthorityLevel.L0,     # Was L1, now autonomous
    DecisionType.UNIVERSE_CURATION: AuthorityLevel.L0, # Was L2, now autonomous
    DecisionType.STRATEGY_KILL: AuthorityLevel.L0,    # Was L4, now autonomous
    DecisionType.FORENSIC: AuthorityLevel.L0,
    DecisionType.CLUSTER_ANALYSIS: AuthorityLevel.L0,
    DecisionType.CONFIG_AUDIT: AuthorityLevel.L0,
    DecisionType.EDGE_DECAY: AuthorityLevel.L0,
    DecisionType.DEPLOY_REVIEW: AuthorityLevel.L0,
}

# Trade count thresholds for authority progression
AUTHORITY_THRESHOLDS = {
    AuthorityLevel.L0: 0,
    AuthorityLevel.L1: 100,
    AuthorityLevel.L2: 300,
    AuthorityLevel.L3: 500,
    AuthorityLevel.L4: 1000,
}


@dataclass
class DecisionRequest:
    """A request for Claude to make a decision."""
    decision_type: DecisionType
    context: Dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    max_tokens: int = 2000
    model: str = "claude-sonnet-4-5-20250929"  # Default to Sonnet for cost

    def to_dict(self) -> dict:
        return {
            "type": self.decision_type.value,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "context_keys": list(self.context.keys()),
        }


@dataclass
class DecisionResponse:
    """Claude's structured response to a decision request."""
    decision_type: DecisionType
    recommendation: str = ""
    confidence: float = 0.0  # 0.0-1.0
    proposed_changes: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    requires_human_approval: bool = True
    model_used: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": self.decision_type.value,
            "recommendation": self.recommendation,
            "confidence": round(self.confidence, 3),
            "proposed_changes": self.proposed_changes,
            "reasoning": self.reasoning[:500],  # Truncate for storage
            "requires_human_approval": self.requires_human_approval,
            "model": self.model_used,
            "tokens": self.tokens_used,
            "cost_usd": round(self.cost_usd, 4),
        }


class DecisionAuthority:
    """Manages Claude's decision authority within the AEGIS V2 system."""

    def __init__(self, trade_count: int = 0, max_daily_cost_usd: float = 5.0):
        self._trade_count = trade_count
        self._max_daily_cost = max_daily_cost_usd
        self._daily_cost = 0.0
        self._decision_log: List[Dict[str, Any]] = []

    @property
    def current_level(self) -> AuthorityLevel:
        """Determine current authority level from trade count."""
        for level in reversed(list(AuthorityLevel)):
            if self._trade_count >= AUTHORITY_THRESHOLDS[level]:
                return level
        return AuthorityLevel.L0

    def can_make_decision(self, decision_type: DecisionType) -> bool:
        """Check if current authority level allows this decision type."""
        required = DECISION_AUTHORITY.get(decision_type, AuthorityLevel.L4)
        return self.current_level.value >= required.value

    def check_budget(self, estimated_cost: float) -> bool:
        """Check if daily budget allows this API call."""
        return self._daily_cost + estimated_cost <= self._max_daily_cost

    def record_cost(self, cost_usd: float):
        """Record API cost."""
        self._daily_cost += cost_usd

    def reset_daily_budget(self):
        """Reset daily cost counter (called at start of each trading day)."""
        self._daily_cost = 0.0

    def get_prompt(
        self,
        decision_type: DecisionType,
        context: Dict[str, Any],
    ) -> DecisionRequest:
        """Generate a decision request with appropriate prompt.

        Uses Book 198 insight: LLMs are filters/classifiers, not signal generators.
        Apply bullish bias correction.
        """
        templates = {
            DecisionType.NIGHTLY_REVIEW: self._nightly_review_prompt,
            DecisionType.REGIME_CHALLENGE: self._regime_challenge_prompt,
            DecisionType.PARAM_CHANGE: self._param_change_prompt,
            DecisionType.ERROR_INVESTIGATION: self._error_investigation_prompt,
            DecisionType.HYPOTHESIS: self._hypothesis_prompt,
            DecisionType.JOURNAL_UPDATE: self._journal_prompt,
            DecisionType.STRATEGY_KILL: self._postmortem_prompt,
            DecisionType.UNIVERSE_CURATION: self._universe_curation_prompt,
            # Book 72: All 14 decision types wired
            DecisionType.FORENSIC: self._forensic_prompt,
            DecisionType.CLUSTER_ANALYSIS: self._cluster_prompt,
            DecisionType.CONFIG_AUDIT: self._config_contradiction_prompt,
            DecisionType.EDGE_DECAY: self._decay_prompt,
            DecisionType.DEPLOY_REVIEW: self._deploy_review_prompt,
        }

        template_fn = templates.get(decision_type, self._generic_prompt)
        prompt = template_fn(context)

        # Model selection (Book 210: cheaper models win)
        model = self._select_model(decision_type)

        return DecisionRequest(
            decision_type=decision_type,
            context=context,
            prompt=prompt,
            model=model,
            max_tokens=2000 if decision_type == DecisionType.NIGHTLY_REVIEW else 1000,
        )

    def _select_model(self, decision_type: DecisionType) -> str:
        """Select cost-appropriate model per Book 210 routing matrix.

        Opus: Critical/creative decisions requiring deep reasoning
        Sonnet: Analytical/investigative decisions
        Haiku: Routine/compression tasks (fast, cheap)
        """
        # Opus (critical reasoning): strategy discovery, post-mortems, error investigation, edge decay
        if decision_type in (
            DecisionType.STRATEGY_KILL, DecisionType.ERROR_INVESTIGATION,
            DecisionType.HYPOTHESIS, DecisionType.EDGE_DECAY,
            DecisionType.CLUSTER_ANALYSIS, DecisionType.UNIVERSE_CURATION,
        ):
            return "claude-opus-4-6"
        # Sonnet (balanced analysis): nightly review, param changes, forensics, config audit, deploy review
        if decision_type in (
            DecisionType.NIGHTLY_REVIEW, DecisionType.PARAM_CHANGE,
            DecisionType.FORENSIC, DecisionType.CONFIG_AUDIT,
            DecisionType.DEPLOY_REVIEW, DecisionType.REGIME_CHALLENGE,
        ):
            return "claude-sonnet-4-5-20250929"
        # Haiku (fast/cheap): journal compression
        return "claude-haiku-4-5-20251001"

    def _nightly_review_prompt(self, ctx: Dict[str, Any]) -> str:
        return (
            "You are the AEGIS V2 nightly forensic reviewer. "
            "Analyze today's trading session and provide structured feedback.\n\n"
            "IMPORTANT: You have a documented BULLISH BIAS (Book 198). "
            "Actively correct for this by giving EXTRA WEIGHT to bearish evidence.\n\n"
            f"Session metrics: {json.dumps(ctx.get('metrics', {}), indent=2)}\n"
            f"Trades: {ctx.get('trade_count', 0)}\n"
            f"Cost-adjusted P&L: {ctx.get('cost_adjusted_pnl', 0):.2f} GBP\n"
            f"Regime: {ctx.get('regime', 'unknown')}\n\n"
            "Output JSON with keys: assessment (1-10), top_issue, "
            "recommended_action, confidence (0-1), reasoning."
        )

    def _regime_challenge_prompt(self, ctx: Dict[str, Any]) -> str:
        return (
            "You are the AEGIS V2 regime challenger. "
            "The automated system classified the current regime. "
            "Challenge this classification with evidence.\n\n"
            f"Automated regime: {ctx.get('regime', 'unknown')}\n"
            f"VIX: {ctx.get('vix', 0)}\n"
            f"HMM state: {ctx.get('hmm_state', -1)}\n\n"
            "Output JSON: agree (bool), alternative_regime, confidence, reasoning."
        )

    def _param_change_prompt(self, ctx: Dict[str, Any]) -> str:
        return (
            "You are the AEGIS V2 parameter skeptic. "
            "A parameter change has been proposed. "
            "Your job is to CHALLENGE it — find reasons NOT to change.\n\n"
            f"Proposed: {json.dumps(ctx.get('proposed_changes', []), indent=2)}\n"
            f"Current values: {json.dumps(ctx.get('current_values', {}), indent=2)}\n"
            f"Statistical basis: n={ctx.get('n_trades', 0)}, "
            f"p={ctx.get('p_value', 1.0):.4f}\n\n"
            "Output JSON: approve (bool), risk_assessment (1-10), "
            "concerns (list), confidence, reasoning."
        )

    def _error_investigation_prompt(self, ctx: Dict[str, Any]) -> str:
        return (
            "You are the AEGIS V2 error investigator. "
            "An anomaly or error has been detected. "
            "Investigate the root cause.\n\n"
            f"Error: {ctx.get('error_description', 'unknown')}\n"
            f"Severity: {ctx.get('severity', 'unknown')}\n"
            f"Context: {json.dumps(ctx.get('error_context', {}), indent=2)}\n\n"
            "Output JSON: root_cause, severity (1-10), "
            "recommended_action, requires_human (bool), reasoning."
        )

    def _hypothesis_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-HYPOTHESIS: Generate new trading strategy ideas from historical data (Book 197)."""
        return (
            "You are the AEGIS V2 strategy discovery agent. "
            "Analyze historical trade patterns and propose a NEW strategy hypothesis.\n\n"
            "IMPORTANT: You have a documented BULLISH BIAS (Book 198). "
            "Actively correct for this by stress-testing every hypothesis against bear markets.\n\n"
            f"Existing strategies: {json.dumps(ctx.get('active_strategies', []))}\n"
            f"Top performing tickers: {json.dumps(ctx.get('top_tickers', []))}\n"
            f"Worst performing patterns: {json.dumps(ctx.get('loss_patterns', []))}\n"
            f"Market regime: {ctx.get('regime', 'unknown')}\n"
            f"Total trades: {ctx.get('trade_count', 0)}\n\n"
            "Propose ONE new strategy. Output JSON with keys: "
            "strategy_name, entry_conditions (list), exit_conditions (list), "
            "expected_edge_pct, confidence (0-1), market_conditions (when it works), "
            "failure_modes (when it fails), backtest_suggestion, reasoning."
        )

    def _journal_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-JOURNAL: Compress institutional memory into searchable learnings (Book 72)."""
        return (
            "You are the AEGIS V2 institutional memory curator. "
            "Compress today's key learnings into searchable insights.\n\n"
            f"Today's trades: {ctx.get('trade_count', 0)}\n"
            f"Net P&L: {ctx.get('net_pnl', 0):.2f} GBP\n"
            f"Key events: {json.dumps(ctx.get('key_events', []))}\n"
            f"Regime: {ctx.get('regime', 'unknown')}\n\n"
            "Output JSON with keys: date, key_learnings (list of max 3), "
            "pattern_discovered (string or null), parameter_insight (string or null), "
            "regime_note (string), confidence (0-1)."
        )

    def _postmortem_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-POSTMORTEM: Analyze why a strategy was killed or performed poorly (Book 72)."""
        return (
            "You are the AEGIS V2 strategy post-mortem analyst. "
            "A strategy has been killed or is critically underperforming. "
            "Analyze the root cause.\n\n"
            f"Strategy: {ctx.get('strategy_name', 'unknown')}\n"
            f"Win rate: {ctx.get('win_rate', 0):.1%}\n"
            f"Profit factor: {ctx.get('profit_factor', 0):.2f}\n"
            f"Total trades: {ctx.get('strategy_trades', 0)}\n"
            f"Kill reason: {ctx.get('kill_reason', 'unknown')}\n"
            f"Trade history sample: {json.dumps(ctx.get('trade_sample', [])[:10])}\n\n"
            "Output JSON with keys: root_cause, was_strategy_sound (bool), "
            "market_changed (bool), implementation_bug (bool), "
            "salvageable_components (list), lessons_for_future (list), confidence (0-1)."
        )

    def _forensic_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-FORENSIC: Deep dive on a single trade (Book 72)."""
        return (
            "You are the AEGIS V2 trade forensic analyst. "
            "Analyze this individual trade end-to-end.\n\n"
            "IMPORTANT: You have a documented BULLISH BIAS (Book 198). "
            "If this was a winner, CHALLENGE whether it was skill or luck.\n\n"
            f"Trade: {json.dumps(ctx.get('trade', {}), indent=2, default=str)}\n"
            f"Entry signal: {ctx.get('entry_strategy', 'unknown')}\n"
            f"Exit reason: {ctx.get('exit_reason', 'unknown')}\n"
            f"P&L: {ctx.get('pnl', 0):.2f} GBP\n"
            f"Duration: {ctx.get('duration_min', 0)} minutes\n"
            f"Spread cost: {ctx.get('spread_cost_pct', 0):.2f}%\n\n"
            "Classify as W1-W5 or L1-L7. Output JSON with keys: "
            "classification (e.g. 'W3' or 'L2'), entry_quality (1-10), "
            "exit_quality (1-10), was_lucky (bool), lesson, confidence (0-1)."
        )

    def _cluster_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-CLUSTER: Root cause analysis of consecutive losses (Book 72)."""
        return (
            "You are the AEGIS V2 loss cluster analyst. "
            "A series of consecutive losses has occurred. "
            "Find the COMMON root cause.\n\n"
            f"Cluster size: {ctx.get('cluster_size', 0)} consecutive losses\n"
            f"Total loss: {ctx.get('total_loss', 0):.2f} GBP\n"
            f"Trades: {json.dumps(ctx.get('trades', [])[:10], default=str)}\n"
            f"Regime during cluster: {ctx.get('regime', 'unknown')}\n"
            f"Strategies involved: {json.dumps(ctx.get('strategies', []))}\n\n"
            "Output JSON with keys: common_root_cause, regime_mismatch (bool), "
            "strategy_issue (string or null), parameter_issue (string or null), "
            "recommended_action, severity (1-10), confidence (0-1)."
        )

    def _config_contradiction_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-CONFIG: Detect contradictions in parameter combinations (Book 72)."""
        return (
            "You are the AEGIS V2 config auditor. "
            "Check the current parameter configuration for internal contradictions.\n\n"
            f"Config snapshot: {json.dumps(ctx.get('config', {}), indent=2, default=str)}\n"
            f"Dynamic weights: {json.dumps(ctx.get('dynamic_weights', {}), indent=2, default=str)}\n"
            f"Active strategies: {json.dumps(ctx.get('active_strategies', []))}\n\n"
            "Look for: conflicting risk limits, impossible constraint combinations, "
            "parameters that cancel each other out, unreachable trigger conditions.\n\n"
            "Output JSON with keys: contradictions (list), severity (1-10), "
            "recommended_fixes (list), confidence (0-1)."
        )

    def _decay_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-DECAY: Detect edge decay in strategies (Book 72)."""
        return (
            "You are the AEGIS V2 edge decay detector. "
            "Analyze whether strategies are losing their edge over time.\n\n"
            f"Strategy performance (30-day rolling):\n{json.dumps(ctx.get('rolling_stats', {}), indent=2, default=str)}\n"
            f"Strategy performance (all-time):\n{json.dumps(ctx.get('alltime_stats', {}), indent=2, default=str)}\n"
            f"Market regime shift: {ctx.get('regime_change', 'none')}\n\n"
            "Output JSON with keys: decaying_strategies (list), healthy_strategies (list), "
            "decay_evidence (dict: strategy → reason), recommended_actions (list), "
            "urgency (1-10), confidence (0-1)."
        )

    def _deploy_review_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-DEPLOY: Pre-deploy code diff safety review (Book 72)."""
        return (
            "You are the AEGIS V2 deployment reviewer. "
            "Review this code change for safety before it goes live.\n\n"
            f"Diff summary: {ctx.get('diff_summary', 'unknown')}\n"
            f"Files changed: {json.dumps(ctx.get('files_changed', []))}\n"
            f"Lines added: {ctx.get('lines_added', 0)}\n"
            f"Lines removed: {ctx.get('lines_removed', 0)}\n\n"
            "Check for: risk gate modifications, position limit changes, "
            "ISA compliance violations, removed safety checks, new dependencies.\n\n"
            "Output JSON with keys: safe_to_deploy (bool), concerns (list), "
            "risk_level (1-10), requires_review (list of files), confidence (0-1)."
        )

    def _universe_curation_prompt(self, ctx: Dict[str, Any]) -> str:
        """D-UNIVERSE: Expand or contract the tradeable universe (Book 72)."""
        return (
            "You are the AEGIS V2 universe curator. "
            "Review the current instrument universe and recommend changes.\n\n"
            f"Current universe size: {ctx.get('universe_size', 0)} contracts\n"
            f"Top performers: {json.dumps(ctx.get('top_performers', [])[:10])}\n"
            f"Worst performers: {json.dumps(ctx.get('worst_performers', [])[:10])}\n"
            f"Untested tickers: {ctx.get('untested_count', 0)}\n"
            f"Market regime: {ctx.get('regime', 'unknown')}\n\n"
            "Output JSON with keys: add_tickers (list), remove_tickers (list), "
            "promote_to_vanguard (list), demote_to_apex (list), "
            "reasoning, confidence (0-1)."
        )

    def _generic_prompt(self, ctx: Dict[str, Any]) -> str:
        return (
            "You are an AEGIS V2 analyst. "
            f"Context: {json.dumps(ctx, indent=2, default=str)}\n"
            "Provide structured analysis as JSON."
        )

    def execute(self, request: DecisionRequest) -> DecisionResponse:
        """Execute a decision request via Claude CLI or Gemini SDK.

        Checks authority level, budget, then calls the appropriate API.
        Falls back to Gemini if Claude CLI is unavailable.
        Returns structured DecisionResponse.
        """
        if not self.can_make_decision(request.decision_type):
            log.warning(
                "Insufficient authority for %s (have %s, need %s)",
                request.decision_type.value,
                self.current_level.name,
                DECISION_AUTHORITY[request.decision_type].name,
            )
            return DecisionResponse(
                decision_type=request.decision_type,
                recommendation="BLOCKED: insufficient authority",
                requires_human_approval=True,
            )

        # Estimate cost: ~$0.003/1K input tokens, $0.015/1K output for Sonnet
        estimated_cost = (request.max_tokens / 1000) * 0.015
        if not self.check_budget(estimated_cost):
            log.warning("Daily budget exhausted ($%.2f / $%.2f)", self._daily_cost, self._max_daily_cost)
            return DecisionResponse(
                decision_type=request.decision_type,
                recommendation="BLOCKED: daily budget exhausted",
                requires_human_approval=True,
            )

        # Try Claude CLI first, then Anthropic API, then Gemini fallback
        response = self._call_claude_cli(request)
        if response is None:
            response = self._call_anthropic_api(request)
        if response is None:
            response = self._call_gemini(request)
        if response is None:
            log.error("All backends failed (CLI + Anthropic API + Gemini)")
            return DecisionResponse(
                decision_type=request.decision_type,
                recommendation="ERROR: all API calls failed",
                requires_human_approval=True,
            )

        self.record_cost(response.cost_usd)
        self._decision_log.append(response.to_dict())
        return response

    def _call_claude_cli(self, request: DecisionRequest) -> Optional[DecisionResponse]:
        """Call Claude via `claude -p` CLI (Book 142: Claude as subprocess)."""
        try:
            result = subprocess.run(
                ["claude", "-p", request.prompt],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "CLAUDE_MODEL": request.model},
            )
            if result.returncode != 0:
                log.warning("Claude CLI failed: %s", result.stderr[:200])
                return None

            raw = result.stdout.strip()
            return self._parse_response(request, raw, model_used=request.model, source="claude")
        except FileNotFoundError:
            log.info("Claude CLI not found, falling back to Gemini")
            return None
        except subprocess.TimeoutExpired:
            log.warning("Claude CLI timed out after 120s")
            return None
        except Exception as e:
            log.error("Claude CLI error: %s", e)
            return None

    def _call_anthropic_api(self, request: DecisionRequest) -> Optional[DecisionResponse]:
        """Call Anthropic API as second fallback (paid, model-tiered)."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            log.info("ANTHROPIC_API_KEY not set, skipping API fallback")
            return None

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)

            # Map request model to API model names
            model_map = {
                "opus": "claude-opus-4-6",
                "sonnet": "claude-sonnet-4-5-20250929",
                "haiku": "claude-haiku-4-5-20251001",
            }
            api_model = model_map.get(request.model, "claude-sonnet-4-5-20250929")

            log.info("Anthropic API call (model=%s, prompt=%d chars)", api_model, len(request.prompt))
            response = client.messages.create(
                model=api_model,
                max_tokens=request.max_tokens,
                messages=[{"role": "user", "content": request.prompt}],
            )

            raw = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw += block.text

            if not raw.strip():
                log.warning("Anthropic API returned empty response")
                return None

            log.info(
                "Anthropic API success: model=%s, input=%d, output=%d tokens",
                api_model, response.usage.input_tokens, response.usage.output_tokens,
            )
            return self._parse_response(request, raw.strip(), model_used=api_model, source="anthropic_api")

        except ImportError:
            log.info("anthropic SDK not installed, skipping API fallback")
            return None
        except Exception as e:
            log.error("Anthropic API error: %s", e)
            return None

    def _call_gemini(self, request: DecisionRequest) -> Optional[DecisionResponse]:
        """Call Gemini API (google-generativeai SDK) as fallback."""
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            log.info("GEMINI_API_KEY not set, skipping Gemini fallback")
            return None

        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(
                request.prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=request.max_tokens,
                    temperature=0.2,
                ),
            )
            raw = response.text.strip()
            return self._parse_response(request, raw, model_used="gemini-2.5-flash", source="gemini")
        except Exception as e:
            log.error("Gemini API error: %s", e)
            return None

    def _parse_response(
        self, request: DecisionRequest, raw: str, model_used: str, source: str,
    ) -> DecisionResponse:
        """Parse raw LLM output into structured DecisionResponse."""
        # Try to parse as JSON (our prompts request JSON output)
        try:
            # Handle markdown code blocks
            clean = raw
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            data = json.loads(clean)
        except (json.JSONDecodeError, IndexError):
            # Fallback: use raw text as recommendation
            data = {"recommendation": raw[:500], "confidence": 0.5}

        # Estimate tokens and cost
        prompt_tokens = len(request.prompt.split()) * 1.3  # rough estimate
        output_tokens = len(raw.split()) * 1.3
        if "gemini" in model_used:
            cost = (prompt_tokens * 0.00015 + output_tokens * 0.0006) / 1000
        else:
            cost = (prompt_tokens * 0.003 + output_tokens * 0.015) / 1000

        return DecisionResponse(
            decision_type=request.decision_type,
            recommendation=data.get("recommendation", data.get("assessment", raw[:200])),
            confidence=float(data.get("confidence", 0.5)),
            proposed_changes=data.get("proposed_changes", []),
            reasoning=data.get("reasoning", ""),
            requires_human_approval=self.current_level.value < AuthorityLevel.L2.value,
            model_used=f"{source}/{model_used}",
            tokens_used=int(prompt_tokens + output_tokens),
            cost_usd=round(cost, 4),
        )

    def to_dict(self) -> dict:
        return {
            "trade_count": self._trade_count,
            "authority_level": self.current_level.value,
            "authority_name": self.current_level.name,
            "daily_cost_usd": round(self._daily_cost, 4),
            "budget_remaining_usd": round(self._max_daily_cost - self._daily_cost, 4),
            "decisions_today": len(self._decision_log),
        }
