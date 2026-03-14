"""
AI Research Engine — NZT-48 W12+ State-of-the-Art Self-Learning
=================================================================
Plugs a large language model (Gemini 2.5 Flash or Claude) into the
self-learning cycle so the system can:

1. PERFORMANCE AUTOPSY — analyse losing streaks by asking AI:
   "Given these 12 losing trades, what academic research explains why
    momentum signals failed? What should we adjust?"

2. PARAMETER RESEARCH — query AI for peer-reviewed justification before
   changing any parameter:
   "Is there academic support for tightening the ADX threshold from 20 to
    25 in TRENDING_UP_STRONG regime for leveraged ETPs?"

3. REGIME PATTERN MATCHING — describe current regime characteristics
   to AI and ask which historical regime archetype it resembles, then
   retrieve the optimal parameter set for that archetype.

4. ANOMALY EXPLANATION — when a ticker underperforms vs the system's
   expectation, ask AI to hypothesise causes using financial theory.

5. CONTINUOUS ACADEMIC SCAN — weekly query for new relevant papers
   published on: momentum trading, leveraged ETPs, market microstructure,
   regime detection, and ISA tax-efficient investing.

Architecture:
  - Uses the project's existing GeminiService / OpenAI API client
  - All queries are structured with a TRADING RESEARCH SYSTEM PROMPT
  - Responses parsed for actionable parameter suggestions
  - Suggestions queued for human review (Telegram) before applying
  - Full audit trail in data/ai_research_log.jsonl

Academic foundation for AI-assisted learning:
  - Silver et al. (2016) "Mastering the Game of Go with Deep Reinforcement
    Learning" — self-play + AI critique loops improve faster than static rules
  - Sutton & Barto (2018) "Reinforcement Learning" (2nd ed.) — policy gradient
    methods benefit from external value function approximators
  - Anthropic (2024) "Constitutional AI: Harmlessness from AI Feedback" —
    AI-as-critic framework for self-improvement with safety constraints
  - OpenAI (2023) "GPT-4 Technical Report" — LLMs can reason about structured
    quantitative data and generate valid research hypotheses
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_FILE = Path("data/ai_research_log.jsonl")
SUGGESTIONS_FILE = Path("data/ai_suggestions_pending.json")

# ─────────────────────────────────────────────────────────────────────────────
# System prompt: establishes the AI's role as a quantitative research assistant
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are an elite quantitative research assistant for an algorithmic trading system
operating in the UK ISA market using leveraged ETPs (3x and 5x).

Your role:
1. Apply peer-reviewed academic finance research to explain trading performance patterns
2. Suggest parameter adjustments backed by specific academic citations
3. Identify regime archetypes from historical market research and match them to current conditions
4. Propose hypotheses for underperformance using financial theory
5. Scan for relevant academic papers on momentum, market microstructure, leveraged ETPs, and regime detection

Trading system context:
- Instruments: LSE leveraged ETPs (QQQ3.L, NVD3.L, TSL3.L, GPT3.L, 3SEM.L, MU2.L, etc.)
- Primary strategy: S15 "2% Daily Target" — find ONE ticker per day capable of a 2% move
- Academic framework: 56+ peer-reviewed papers including Jegadeesh & Titman (1993),
  Brock et al. (1992), Brunnermeier & Pedersen (2009), Bernard & Thomas (1989), etc.
- Live metrics: RVOL, ADX, confidence score, 8-indicator consensus, VWAP, sector momentum
- Current self-learning: LightGBM + XGBoost + PA ensemble, Bayesian win rate, drift detection

Rules for your responses:
- ALWAYS cite specific academic papers (author, year, journal)
- Be concrete: suggest specific threshold changes, not vague directions
- Distinguish between "well-replicated finding" vs "preliminary research"
- Flag if a suggestion conflicts with existing academic basis in the system
- Format suggestions as JSON at the end: {"parameter": "...", "change": ..., "confidence": "HIGH/MED/LOW", "citation": "..."}
"""


class AIResearchEngine:
    """
    AI-powered research and self-improvement engine.

    Wraps any available LLM (Gemini, Claude, OpenAI) and provides
    structured queries for autonomous trading system improvement.

    All suggestions require human Telegram approval before application.
    Full audit trail maintained in data/ai_research_log.jsonl.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self._client = None
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        SUGGESTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _get_client(self):
        """Lazy-initialise LLM client. Tries Gemini first, then OpenAI."""
        if self._client:
            return self._client

        # Try Gemini
        try:
            import google.generativeai as genai
            api_key = os.environ.get("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
                self._client = ("gemini", genai.GenerativeModel(
                    model_name="gemini-2.5-flash",
                    system_instruction=_SYSTEM_PROMPT,
                ))
                logger.info("AIResearchEngine: using Gemini 2.5 Flash")
                return self._client
        except Exception:
            pass

        # Try OpenAI
        try:
            from openai import OpenAI
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key:
                self._client = ("openai", OpenAI(api_key=api_key))
                logger.info("AIResearchEngine: using OpenAI")
                return self._client
        except Exception:
            pass

        logger.warning("AIResearchEngine: no LLM client available (set GEMINI_API_KEY or OPENAI_API_KEY)")
        return None

    def _call_ai(self, prompt: str, context: str = "") -> Optional[str]:
        """
        Calls the AI with a structured research prompt.
        Returns text response or None on failure.
        """
        client_info = self._get_client()
        if not client_info:
            return None

        client_type, client = client_info
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        try:
            if client_type == "gemini":
                response = client.generate_content(full_prompt)
                return response.text

            elif client_type == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": full_prompt},
                    ],
                    max_tokens=2048,
                    temperature=0.3,  # Low temperature for factual research responses
                )
                return response.choices[0].message.content

        except Exception as e:
            logger.warning("AIResearchEngine._call_ai failed: %s", e)
            return None

    def _log_query(self, query_type: str, prompt: str, response: Optional[str],
                   context_summary: str = "") -> None:
        """Appends query+response to audit log."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query_type": query_type,
            "context_summary": context_summary,
            "prompt_excerpt": prompt[:200],
            "response_excerpt": response[:500] if response else None,
            "model": self.model,
        }
        try:
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug("AIResearchEngine: log write failed: %s", e)

    def _extract_suggestions(self, response: str) -> list:
        """Parse JSON suggestion blocks from AI response."""
        suggestions = []
        if not response:
            return suggestions
        import re
        # Find JSON blocks
        matches = re.findall(r'\{[^{}]*"parameter"[^{}]*\}', response, re.DOTALL)
        for m in matches:
            try:
                obj = json.loads(m)
                if "parameter" in obj and "change" in obj:
                    obj["extracted_at"] = datetime.now(timezone.utc).isoformat()
                    suggestions.append(obj)
            except Exception:
                continue
        return suggestions

    def _queue_suggestion(self, suggestion: dict, source: str) -> None:
        """Adds suggestion to pending queue for Telegram review."""
        pending = []
        if SUGGESTIONS_FILE.exists():
            try:
                with open(SUGGESTIONS_FILE) as f:
                    pending = json.load(f)
            except Exception:
                pending = []
        suggestion["source"] = source
        suggestion["status"] = "PENDING_REVIEW"
        pending.append(suggestion)
        try:
            with open(SUGGESTIONS_FILE, "w") as f:
                json.dump(pending, f, indent=2)
        except Exception as e:
            logger.warning("AIResearchEngine: queue suggestion failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # CORE QUERY TYPES
    # ─────────────────────────────────────────────────────────

    def performance_autopsy(self, losing_trades: list, current_regime: str,
                             recent_win_rate: float) -> Optional[str]:
        """
        Analyses losing streaks and asks AI for academic explanation + fix.

        Called automatically when: win_rate drops >8% in 20-trade window
        OR 3+ consecutive losses in same session.

        Silver et al. (2016): self-critique loops accelerate improvement.
        """
        if not losing_trades:
            return None

        # Build context from losing trades
        trade_summary = []
        for t in losing_trades[-10:]:  # Last 10 losers
            trade_summary.append({
                "ticker": t.get("ticker"),
                "r_multiple": t.get("r_multiple"),
                "regime": t.get("regime"),
                "confidence": t.get("confidence"),
                "rvol": t.get("rvol"),
                "adx": t.get("adx"),
                "exit_reason": t.get("exit_reason"),
            })

        context = f"""
RECENT PERFORMANCE DATA:
- Current regime: {current_regime}
- Recent win rate: {recent_win_rate:.1%}
- Sample losing trades (last 10): {json.dumps(trade_summary, indent=2)}
"""

        prompt = f"""Analyse these losing trades and explain:
1. What academic research explains this pattern of losses?
2. Is this regime-specific (i.e., is {current_regime} known to be difficult for momentum)?
3. What specific parameter adjustments are supported by peer-reviewed research?
4. Are any of the 8 indicators (RSI, MACD, EMA9/20/50, VWAP, StochRSI, OBV) particularly
   miscalibrated for leveraged ETPs in this regime?
5. Should we widen stops, tighten consensus requirements, or reduce size?

Provide 2-3 concrete suggestions in JSON format at the end."""

        response = self._call_ai(prompt, context)
        if response:
            self._log_query("performance_autopsy", prompt, response,
                           f"regime={current_regime} wr={recent_win_rate:.1%} n={len(losing_trades)}")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "performance_autopsy")
            logger.info("AIResearchEngine: autopsy complete — %d suggestions queued", len(suggestions))
        return response

    def parameter_research_query(self, parameter: str, current_value,
                                  proposed_value, context: str = "") -> Optional[str]:
        """
        Queries AI for academic support before changing any parameter.

        Called from learning_engine.py before applying parameter updates.
        If AI returns LOW confidence, human confirmation required.
        """
        prompt = f"""Evaluate this parameter change for our leveraged ETP momentum system:

Parameter: {parameter}
Current value: {current_value}
Proposed value: {proposed_value}
Context: {context}

Questions:
1. Is there peer-reviewed academic support for this change direction?
2. Does the evidence apply specifically to leveraged ETPs / high-beta instruments?
3. What is the risk of overfitting (i.e., is this sample-size-justified)?
4. Are there known failure modes for this change in certain regimes?
5. What monitoring should we implement after applying this change?

Rate your confidence: HIGH (multiple papers, directly applicable),
MED (some evidence, partially applicable), LOW (indirect evidence only).

Provide your recommendation as JSON at the end."""

        response = self._call_ai(prompt)
        if response:
            self._log_query("parameter_research", prompt, response,
                           f"param={parameter} {current_value}→{proposed_value}")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "parameter_research")
        return response

    def regime_archetype_query(self, regime_characteristics: dict) -> Optional[str]:
        """
        Describes current regime to AI and asks which historical archetype it matches.
        Returns optimal parameter set for that archetype.

        Ang & Bekaert (2002): regime-switching models have well-documented archetypes.
        """
        context = f"""
CURRENT REGIME CHARACTERISTICS:
{json.dumps(regime_characteristics, indent=2)}
"""

        prompt = """Match these market characteristics to known historical regime archetypes from
academic literature. For each archetype match:

1. Name the archetype (e.g., "2019 QE-driven NASDAQ grind", "2020 COVID shock recovery",
   "2022 rate-hike rotation")
2. Cite the academic literature that characterises this regime type
   (e.g., Ang & Bekaert 2002, Guidolin & Timmermann 2007)
3. What did momentum / leveraged ETP strategies return during this archetype?
4. What parameter adjustments produced best risk-adjusted returns?
5. What was the typical regime duration and transition signal?

Focus on: momentum persistence, volatility clustering, sector rotation patterns,
correlation breakdown, and liquidity regimes.

Provide parameter recommendations as JSON."""

        response = self._call_ai(prompt, context)
        if response:
            self._log_query("regime_archetype", prompt, response,
                           f"regime_chars={str(regime_characteristics)[:100]}")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "regime_archetype")
        return response

    def anomaly_explanation_query(self, ticker: str, expected_r: float,
                                   actual_r: float, trade_context: dict) -> Optional[str]:
        """
        When a ticker significantly underperforms its expected R-multiple,
        asks AI to hypothesise causes using financial theory.

        Bali et al. (2011): MAX anomaly and tail risk in individual stocks.
        """
        deviation = actual_r - expected_r
        context = f"""
TRADE ANOMALY:
- Ticker: {ticker}
- Expected R-multiple: {expected_r:.2f}
- Actual R-multiple: {actual_r:.2f}
- Deviation: {deviation:+.2f}R
- Trade context: {json.dumps(trade_context, indent=2)}
"""

        prompt = f"""Hypothesise why {ticker} underperformed its expected R-multiple by {deviation:.2f}R.

Consider:
1. Was there a known microstructure effect (e.g., expiry pinning, gap exhaustion,
   VWAP magnet effect, dark pool activity)?
2. Does this match any documented anomaly for leveraged ETPs specifically?
   (e.g., volatility drag, rebalancing mechanics, decay during sideways moves)
3. Are there sector/macro factors that could explain this specific outcome?
4. What signal(s) in our indicator set should have been more predictive?
5. How should this outcome update our model for similar future setups?

Academic references: Bali, Cakici & Whitelaw (2011), Ben-David et al. (2018),
Cheng & Madhavan (2009) "The Dynamics of Leveraged and Inverse Exchange Traded Funds"

Provide adjustment suggestions as JSON."""

        response = self._call_ai(prompt, context)
        if response:
            self._log_query("anomaly_explanation", prompt, response,
                           f"ticker={ticker} expected={expected_r:.2f} actual={actual_r:.2f}")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "anomaly_explanation")
        return response

    def weekly_academic_scan(self) -> Optional[str]:
        """
        Weekly scan for new academic papers relevant to the trading system.

        Topics: momentum trading, leveraged ETPs, market microstructure,
        regime detection, ISA tax-efficient investing, UK equity markets.

        Called Sunday 22:00 UTC in the weekly auto-improvement cycle.
        """
        prompt = """Identify the most relevant recent academic research (2020-2025) for a UK ISA
leveraged ETP momentum trading system. Focus on papers that are:
1. Directly applicable to LSE-listed leveraged products (3x/5x ETPs)
2. Regime detection or regime-switching methods for short-term trading
3. New evidence on momentum/trend-following in high-volatility instruments
4. Market microstructure papers on leveraged product mechanics
5. Machine learning for financial time series that would improve our ensemble model

For each paper provide:
- Full citation (author, year, journal, DOI if available)
- Key finding in 1 sentence
- Specific applicability to our system
- Priority: HIGH/MED/LOW for implementation

Also: are there any recent replication failures of papers we cite?
(e.g., momentum anomaly, PEAD, short squeeze effects)

Format new parameter suggestions as JSON if any papers suggest concrete changes."""

        response = self._call_ai(prompt)
        if response:
            self._log_query("weekly_academic_scan", prompt, response, "weekly_scan")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "weekly_academic_scan")
            logger.info("AIResearchEngine: weekly scan complete — %d suggestions queued", len(suggestions))
        return response

    def indicator_calibration_query(self, indicator_effectiveness: dict,
                                     regime: str) -> Optional[str]:
        """
        Given per-indicator win-rate contributions from the learning engine,
        asks AI how to recalibrate weights.

        Called when indicator effectiveness matrix shows >15% divergence
        from expected contributions.
        """
        context = f"""
INDICATOR EFFECTIVENESS IN {regime} REGIME:
{json.dumps(indicator_effectiveness, indent=2)}

(Values show win rate contribution: positive = helpful, negative = harmful)
"""

        prompt = f"""Our 8-indicator consensus system shows these effectiveness scores in {regime} regime.

Questions:
1. Which indicators are theoretically expected to be weak in {regime} conditions?
   (e.g., RSI is known to lag in strong trends — Wilder 1978; EMAs cross-signal
   in choppy conditions — Alexander & Rader 1989)
2. For leveraged ETPs specifically, which indicators need recalibration?
   (Ben-David et al. 2018: rebalancing mechanics distort OBV and Stoch RSI)
3. Should we reduce the weight of consistently negative indicators to below 1/8 vote?
4. Are there replacement indicators better suited for leveraged ETPs in this regime?
   (e.g., VWAP ratio, ATR percentile rank, relative sector momentum)
5. What is the academic basis for adjusting indicator thresholds vs weights?

Provide specific recalibration suggestions as JSON with exact threshold values."""

        response = self._call_ai(prompt, context)
        if response:
            self._log_query("indicator_calibration", prompt, response,
                           f"regime={regime}")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "indicator_calibration")
        return response

    def self_assessment_query(self, full_performance_stats: dict) -> Optional[str]:
        """
        Comprehensive self-assessment: feeds full system stats to AI and
        asks for a "fund manager review" of the entire strategy.

        Called monthly. Output goes to Telegram as "AI Strategy Review".
        """
        context = f"""
FULL SYSTEM PERFORMANCE STATISTICS:
{json.dumps(full_performance_stats, indent=2)}
"""

        prompt = """Conduct a comprehensive fund manager review of this algorithmic trading system.

Evaluate:
1. EDGE QUALITY: Is the net expectancy sustainable? What academic evidence supports
   the current edge (momentum, PEAD, VWAP, sector momentum)?
2. RISK MANAGEMENT: Are stops, position sizing, and Kelly fraction appropriate for
   leveraged ETPs? Compare to academic optimal sizing literature.
3. REGIME SENSITIVITY: Which regimes produce edge? Which destroy it? Is diversification
   across regime types adequate?
4. LEARNING RATE: Is the system adapting fast enough? Compare to academic benchmarks
   for online learning convergence rates.
5. BLIND SPOTS: What known risk factors are not modelled?
   (e.g., liquidity black holes, overnight gaps, earnings surprises)
6. COMPARISON: How does this system compare to academic descriptions of successful
   systematic momentum strategies (Asness et al. 2013, AQR research)?
7. IMPROVEMENT PRIORITY: What single change would most improve risk-adjusted returns
   based on peer-reviewed evidence?

Be honest and critical. Provide all suggestions as JSON."""

        response = self._call_ai(prompt, context)
        if response:
            self._log_query("self_assessment", prompt, response,
                           f"n_trades={full_performance_stats.get('total_trades', 0)}")
            suggestions = self._extract_suggestions(response)
            for s in suggestions:
                self._queue_suggestion(s, "monthly_self_assessment")
            logger.info("AIResearchEngine: self-assessment complete — %d suggestions queued", len(suggestions))
        return response

    # ─────────────────────────────────────────────────────────
    # Pending suggestions management
    # ─────────────────────────────────────────────────────────

    def get_pending_suggestions(self) -> list:
        """Returns all pending AI suggestions awaiting human review."""
        if not SUGGESTIONS_FILE.exists():
            return []
        try:
            with open(SUGGESTIONS_FILE) as f:
                return [s for s in json.load(f) if s.get("status") == "PENDING_REVIEW"]
        except Exception:
            return []

    def approve_suggestion(self, parameter: str) -> Optional[dict]:
        """
        Marks suggestion as approved. Returns the suggestion dict.
        Called when operator sends 'APPROVE <parameter>' via Telegram.
        """
        if not SUGGESTIONS_FILE.exists():
            return None
        try:
            with open(SUGGESTIONS_FILE) as f:
                all_suggestions = json.load(f)
            for s in all_suggestions:
                if s.get("parameter") == parameter and s.get("status") == "PENDING_REVIEW":
                    s["status"] = "APPROVED"
                    s["approved_at"] = datetime.now(timezone.utc).isoformat()
                    with open(SUGGESTIONS_FILE, "w") as f:
                        json.dump(all_suggestions, f, indent=2)
                    logger.info("AIResearchEngine: approved suggestion for %s", parameter)
                    return s
        except Exception as e:
            logger.warning("AIResearchEngine: approve_suggestion failed: %s", e)
        return None

    def reject_suggestion(self, parameter: str) -> bool:
        """Marks suggestion as rejected."""
        if not SUGGESTIONS_FILE.exists():
            return False
        try:
            with open(SUGGESTIONS_FILE) as f:
                all_suggestions = json.load(f)
            for s in all_suggestions:
                if s.get("parameter") == parameter and s.get("status") == "PENDING_REVIEW":
                    s["status"] = "REJECTED"
                    s["rejected_at"] = datetime.now(timezone.utc).isoformat()
            with open(SUGGESTIONS_FILE, "w") as f:
                json.dump(all_suggestions, f, indent=2)
            return True
        except Exception:
            return False

    def get_telegram_suggestions_summary(self) -> str:
        """Formats pending suggestions for Telegram review."""
        pending = self.get_pending_suggestions()
        if not pending:
            return "🤖 AI Research: no pending suggestions"

        lines = [f"🤖 AI Research Engine — {len(pending)} suggestion(s) pending:"]
        for s in pending[:5]:  # Show max 5
            conf = s.get("confidence", "?")
            param = s.get("parameter", "?")
            change = s.get("change", "?")
            citation = s.get("citation", "")[:50]
            source = s.get("source", "?")
            emoji = "🟢" if conf == "HIGH" else "🟡" if conf == "MED" else "🟠"
            lines.append(
                f"  {emoji} [{conf}] {param}: {change}\n"
                f"      Source: {source} | {citation}\n"
                f"      Reply: APPROVE {param} or REJECT {param}"
            )
        if len(pending) > 5:
            lines.append(f"  ... and {len(pending)-5} more")
        return "\n".join(lines)

    def is_available(self) -> bool:
        """Returns True if an LLM client is available."""
        return self._get_client() is not None

    def get_status(self) -> dict:
        """Status dict for war room display."""
        pending = self.get_pending_suggestions()
        log_lines = 0
        if LOG_FILE.exists():
            try:
                log_lines = sum(1 for _ in open(LOG_FILE))
            except Exception:
                pass
        return {
            "available": self.is_available(),
            "model": self.model,
            "pending_suggestions": len(pending),
            "total_queries": log_lines,
        }
