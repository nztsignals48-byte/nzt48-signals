"""Claude Curator / Challenger — intelligence layer for AEGIS V2 signal evaluation.

Provides pre-trade signal review, post-trade forensic analysis, batch evaluation
for backtesting, and daily plan curation. All calls go through `claude -p` CLI
(Max subscription on EC2, $0/month).

DOCTRINE:
    - Claude operates exclusively on the COLD PATH.
    - Zero Positive Authority: Claude may downrank, veto, challenge, explain.
      Claude may NOT force trades, override risk gates, mutate live config.
    - All interactions are advisory — the 33-CHECK Rust RiskArbiter is final authority.
    - Mathematical Supremacy: base all analysis on WAL events, P&L, MFE/MAE, spread drag.

Architecture:
    - `evaluate_signal()` — pre-trade: score a single signal (live mode, <5s target)
    - `post_trade_analysis()` — post-trade: classify and extract lessons
    - `batch_evaluate()` — backtest: evaluate 100+ signals in a single Claude call
    - `curate_daily_plan()` — morning: pre-market analysis and ticker focus

Usage:
    from python_brain.ouroboros.claude_curator import evaluate_signal, post_trade_analysis

    verdict = evaluate_signal(signal_dict, market_context)
    # verdict = {"claude_verdict": "approve", "adjusted_confidence": 78, ...}

    analysis = post_trade_analysis(trade_result)
    # analysis = {"classification": "L3", "lessons": [...], ...}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("claude_curator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLAUDE_CMD = ["claude", "-p"]
TIMEOUT_SINGLE = 30       # seconds for single signal evaluation
TIMEOUT_BATCH = 120        # seconds for batch evaluation
TIMEOUT_ANALYSIS = 60      # seconds for post-trade / daily plan
MAX_RETRIES = 3
BACKOFF_BASE = 2           # exponential backoff: 2^attempt seconds

# Cache settings — avoid re-evaluating identical signals
CACHE_MAX_SIZE = 500
CACHE_TTL_SECS = 300       # 5 minutes

# Simulation mode — returns mock responses when Claude CLI is unavailable
SIMULATION_MODE = os.environ.get("CLAUDE_CURATOR_SIM", "0") == "1"

# Log file for all Claude interactions (NDJSON)
LOG_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
LOG_FILE = LOG_DIR / "claude_curator.ndjson"

# ---------------------------------------------------------------------------
# Response cache — LRU with TTL
# ---------------------------------------------------------------------------
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _cache_key(signal: dict, context: dict) -> str:
    """Generate a deterministic cache key from signal + context."""
    # Use only the fields that affect evaluation outcome
    key_data = {
        "ticker": signal.get("ticker_id") or signal.get("ticker", ""),
        "symbol": signal.get("symbol", ""),
        "direction": signal.get("direction", ""),
        "confidence": signal.get("confidence", 0),
        "kelly_fraction": round(signal.get("kelly_fraction", 0), 4),
        "strategy": signal.get("strategy", ""),
        "hurst_regime": signal.get("hurst_regime", ""),
        "regime": context.get("regime", ""),
    }
    raw = json.dumps(key_data, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> Optional[dict]:
    """Retrieve from cache if present and not expired."""
    if key in _cache:
        ts, value = _cache[key]
        if time.time() - ts < CACHE_TTL_SECS:
            _cache.move_to_end(key)
            return value
        else:
            del _cache[key]
    return None


def _cache_put(key: str, value: dict) -> None:
    """Store in cache, evicting oldest if over capacity."""
    _cache[key] = (time.time(), value)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX_SIZE:
        _cache.popitem(last=False)


# ---------------------------------------------------------------------------
# NDJSON interaction logger
# ---------------------------------------------------------------------------
def _log_interaction(
    action: str,
    prompt_chars: int,
    response: Optional[dict],
    latency_ms: float,
    error: str = "",
    cached: bool = False,
) -> None:
    """Append a record to claude_curator.ndjson for audit trail."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "prompt_chars": prompt_chars,
        "latency_ms": round(latency_ms, 1),
        "cached": cached,
        "error": error,
        "response_keys": list(response.keys()) if response else [],
    }
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        log.warning("Failed to write curator log: %s", e)


# ---------------------------------------------------------------------------
# Claude CLI subprocess wrapper
# ---------------------------------------------------------------------------
def _call_claude(prompt: str, timeout: int = TIMEOUT_SINGLE) -> Optional[str]:
    """Call `claude -p` and return the raw response text.

    Retries up to MAX_RETRIES times with exponential backoff.
    Returns None on all-retries exhausted.
    """
    cmd = CLAUDE_CMD + ["--output-format", "json"]

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            log.info(
                "Claude CLI call (attempt %d/%d, timeout=%ds, prompt=%d chars)",
                attempt + 1, MAX_RETRIES, timeout, len(prompt),
            )
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("AEGIS_ROOT", "/app"),
            )

            if result.returncode != 0:
                last_error = (result.stderr or "")[:500] or f"exit code {result.returncode}"
                log.warning(
                    "Claude CLI error (attempt %d/%d): %s",
                    attempt + 1, MAX_RETRIES, last_error,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE ** attempt)
                continue

            output = result.stdout.strip()
            if not output:
                last_error = "empty stdout"
                log.warning("Claude CLI returned empty output (attempt %d/%d)", attempt + 1, MAX_RETRIES)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE ** attempt)
                continue

            # Extract content from Claude CLI JSON envelope
            extracted = _extract_content(output)
            if extracted:
                return extracted
            # Fallback: return raw output
            return output

        except subprocess.TimeoutExpired:
            last_error = f"timeout after {timeout}s"
            log.error("Claude CLI timeout (%ds, attempt %d/%d)", timeout, attempt + 1, MAX_RETRIES)
        except OSError as e:
            last_error = str(e)
            log.error("Claude CLI OS error: %s", e)
        except Exception as e:
            last_error = str(e)
            log.error("Claude CLI unexpected error: %s", e)

        if attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_BASE ** attempt)

    log.error("Claude CLI: all %d retries exhausted. Last error: %s", MAX_RETRIES, last_error)
    return None


def _extract_content(raw_output: str) -> Optional[str]:
    """Extract the actual text content from Claude CLI JSON envelope.

    Claude CLI with --output-format json wraps the response:
    {"type":"result","result":"<actual content>","...}
    """
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        return raw_output  # Not JSON — return as-is

    if isinstance(parsed, dict) and parsed.get("type") == "result" and "result" in parsed:
        inner = parsed["result"]
        if isinstance(inner, str):
            return inner.strip()
        elif isinstance(inner, dict):
            return json.dumps(inner)
        return str(inner)

    return raw_output


def _parse_json_from_text(text: str) -> Optional[dict]:
    """Parse JSON from Claude's response, handling markdown code fences."""
    if not text:
        return None

    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.split("```json", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
        cleaned = cleaned.strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object within the text
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(cleaned[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Simulation mode — deterministic mock responses
# ---------------------------------------------------------------------------
def _sim_evaluate_signal(signal: dict, context: dict) -> dict:
    """Return a deterministic mock verdict for simulation/testing."""
    confidence = signal.get("confidence", 50)
    kelly = signal.get("kelly_fraction", 0.10)

    # Simple heuristic: approve if confidence >= 65, otherwise reject
    if confidence >= 65:
        verdict = "approve"
        adj_conf = confidence
    elif confidence >= 50:
        verdict = "approve"
        adj_conf = confidence - 5  # slight haircut
    else:
        verdict = "reject"
        adj_conf = confidence

    return {
        "claude_verdict": verdict,
        "adjusted_confidence": adj_conf,
        "adjusted_kelly": round(kelly * (adj_conf / max(confidence, 1)), 4),
        "reasoning": f"[SIMULATION] Confidence {confidence} {'meets' if verdict == 'approve' else 'below'} threshold. No real Claude analysis performed.",
        "risk_flags": [],
        "latency_ms": 0.0,
        "cached": False,
        "simulation": True,
    }


def _sim_post_trade(trade: dict) -> dict:
    """Return a mock post-trade analysis for simulation/testing."""
    pnl = trade.get("pnl", 0)
    classification = "W1" if pnl > 0 else "L1" if pnl < 0 else "BREAKEVEN"
    return {
        "classification": classification,
        "narrative": f"[SIMULATION] Trade PnL={pnl:.2f}. No real Claude analysis.",
        "lessons": ["Simulation mode — no Claude analysis available"],
        "pattern": "unknown",
        "suggested_adjustments": [],
        "confidence": 0.0,
        "simulation": True,
    }


def _sim_batch_evaluate(signals: list) -> list:
    """Return mock batch verdicts for simulation/testing."""
    results = []
    for sig in signals:
        ctx = sig.get("_context", {})
        results.append(_sim_evaluate_signal(sig, ctx))
    return results


def _sim_daily_plan(ticker_scores: dict, regime: str, performance: dict) -> dict:
    """Return a mock daily plan for simulation/testing."""
    # Sort tickers by score and take top 10
    sorted_tickers = sorted(ticker_scores.items(), key=lambda x: x[1], reverse=True)[:10]
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "simulation",
        "regime_assessment": f"[SIMULATION] Regime={regime}. No real Claude analysis.",
        "focus_tickers": [t[0] for t in sorted_tickers],
        "strategy_weights": {"F_MOM": 0.4, "F_REV": 0.3, "F_MAC": 0.2, "F_DIS": 0.1},
        "risk_notes": ["Simulation mode — no Claude analysis available"],
        "confidence": 0.0,
        "simulation": True,
    }


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
_SIGNAL_EVAL_SYSTEM = """You are the AEGIS V2 signal curator — a pre-trade intelligence filter for an autonomous UK ISA leveraged ETP trading engine.

ROLE: Review a trade signal BEFORE execution. Score it. Approve or reject.
AUTHORITY: Advisory only. You may downrank or reject. You cannot force trades or override the 33-CHECK Rust RiskArbiter.

CONTEXT:
- System trades 3x/5x leveraged ETPs across LSE, US, HK, TSE, XETRA, EURONEXT, SGX.
- Strategies: TypeA-F (TypeA=dip recovery, TypeB=early runner, TypeC=exhaustion, TypeD=bounce, TypeE=capitulation, TypeF=OBV divergence), Orchestrator (VWAP dip/Gap fade/RSI-IBS/Cross-market).
- Exit: 5-rung Chandelier trailing stop with adaptive ATR multipliers.
- Risk: 33-check arbiter, confidence floor, spread veto, portfolio heat, ISA limits.
- Phase: Paper trading, GBP 10,000 starting equity.

OUTPUT FORMAT (pure JSON, no markdown wrapping):
{
  "claude_verdict": "approve" | "reject" | "reduce",
  "adjusted_confidence": <int 0-100>,
  "adjusted_kelly": <float 0.0-0.35>,
  "reasoning": "<1-3 sentences explaining the decision>",
  "risk_flags": ["<any risk concerns>"],
  "pattern_match": "<W1-W5 expected winner pattern or L1-L7 risk pattern, if recognizable>"
}

DECISION CRITERIA:
- APPROVE if: strong trend alignment, reasonable spread, volume confirming, regime-appropriate strategy.
- REDUCE if: signal has merit but elevated risk (late in session, mixed regime, thin volume). Lower confidence by 5-15.
- REJECT if: regime mismatch (momentum in mean-reverting), chasing extension, spread too wide for edge, conflicting timeframes, low structural quality.

Be concise. Be mathematical. No narratives without data."""


def _build_signal_prompt(signal: dict, context: dict) -> str:
    """Build the prompt for single signal evaluation."""
    # Extract key signal fields
    parts = [_SIGNAL_EVAL_SYSTEM, "\n\n--- SIGNAL TO EVALUATE ---"]

    sig_summary = {
        "ticker": signal.get("symbol", signal.get("ticker_id", "unknown")),
        "direction": signal.get("direction", "Long"),
        "confidence": signal.get("confidence", 0),
        "kelly_fraction": round(signal.get("kelly_fraction", 0), 4),
        "shares": signal.get("shares", 0),
        "strategy": signal.get("strategy", "unknown"),
    }
    parts.append(json.dumps(sig_summary, indent=2))

    # Indicator snapshot
    indicators = {}
    for key in ("rvol", "hurst", "hurst_regime", "adx", "vol_slope",
                "volume_divergence", "vwap_dist_pct", "structural_score"):
        val = signal.get(key)
        if val is not None:
            indicators[key] = round(val, 4) if isinstance(val, float) else val
    if indicators:
        parts.append("\n--- INDICATORS ---")
        parts.append(json.dumps(indicators, indent=2))

    # Market context
    if context:
        ctx_summary = {}
        for key in ("regime", "vix", "spy_return", "session", "exchange",
                    "drawdown_pct", "equity", "open_positions", "trades_today",
                    "consecutive_losses", "daily_pnl", "spread_pct"):
            val = context.get(key)
            if val is not None:
                ctx_summary[key] = round(val, 4) if isinstance(val, float) else val
        if ctx_summary:
            parts.append("\n--- MARKET CONTEXT ---")
            parts.append(json.dumps(ctx_summary, indent=2))

    parts.append("\n\nEvaluate this signal. Return pure JSON.")
    return "\n".join(parts)


_POST_TRADE_SYSTEM = """You are the AEGIS V2 post-trade forensic analyst for an autonomous UK ISA leveraged ETP trading engine.

ROLE: Analyze a completed trade. Classify it. Extract lessons. Identify patterns.
BASE ALL ANALYSIS ON THE DATA PROVIDED. Do not invent narratives.

TRADE CLASSIFICATION TAXONOMY:
Winners: W1 (Clean Trend), W2 (Grind), W3 (Rung Climber), W4 (VWAP Reclaim), W5 (Macro Surf)
Losers: L1 (Spread Victim), L2 (Stop Hunted), L3 (Late Entry), L4 (Macro Crush), L5 (Regime Mismatch), L6 (Fake Breakout), L7 (Time Decay)

OUTPUT FORMAT (pure JSON, no markdown wrapping):
{
  "classification": "<W1-W5 or L1-L7>",
  "narrative": "<2-4 sentences: what happened, why it won/lost>",
  "lessons": ["<lesson 1>", "<lesson 2>"],
  "pattern": "<recurring pattern name if recognized, else 'novel'>",
  "root_cause": "<primary reason for outcome>",
  "suggested_adjustments": [
    {"parameter": "<config key>", "direction": "tighten|loosen", "reasoning": "<why>"}
  ],
  "mfe_utilization": "<percentage of MFE captured, if data available>",
  "confidence": 0.0-1.0
}

Be concise. Be mathematical."""


def _build_post_trade_prompt(trade: dict) -> str:
    """Build the prompt for post-trade analysis."""
    parts = [_POST_TRADE_SYSTEM, "\n\n--- COMPLETED TRADE ---"]

    trade_summary = {}
    for key in ("ticker", "symbol", "direction", "strategy", "confidence",
                "kelly_fraction", "entry_price", "exit_price", "pnl", "pnl_pct",
                "duration_secs", "duration_bars", "rungs_achieved", "max_rung",
                "mae", "mfe", "mae_pct", "mfe_pct", "spread_at_entry",
                "spread_at_exit", "entry_time", "exit_time", "exit_reason",
                "regime_at_entry", "vix_at_entry"):
        val = trade.get(key)
        if val is not None:
            trade_summary[key] = round(val, 4) if isinstance(val, float) else val
    parts.append(json.dumps(trade_summary, indent=2))

    # Entry indicators (at time of signal)
    entry_indicators = trade.get("entry_indicators", {})
    if entry_indicators:
        parts.append("\n--- ENTRY INDICATORS ---")
        parts.append(json.dumps(
            {k: round(v, 4) if isinstance(v, float) else v for k, v in entry_indicators.items()},
            indent=2,
        ))

    parts.append("\n\nClassify this trade. Return pure JSON.")
    return "\n".join(parts)


_BATCH_SYSTEM = """You are the AEGIS V2 signal curator evaluating a BATCH of trade signals for backtesting.

ROLE: For each signal, provide approve/reject/reduce with adjusted confidence.
Be concise — one JSON object per signal in an array.

OUTPUT FORMAT (pure JSON array, no markdown wrapping):
[
  {
    "index": 0,
    "claude_verdict": "approve" | "reject" | "reduce",
    "adjusted_confidence": <int 0-100>,
    "reasoning": "<1 sentence>"
  },
  ...
]

DECISION CRITERIA:
- APPROVE: strong trend alignment, volume confirming, regime-appropriate.
- REDUCE: merit but elevated risk. Lower confidence 5-15 points.
- REJECT: regime mismatch, chasing, spread kill, conflicting timeframes.

Be concise. Return ONLY the JSON array."""


def _build_batch_prompt(signals: list) -> str:
    """Build a single prompt for batch signal evaluation."""
    parts = [_BATCH_SYSTEM, "\n\n--- SIGNALS TO EVALUATE ---\n"]

    for i, sig in enumerate(signals):
        entry = {
            "index": i,
            "ticker": sig.get("symbol", sig.get("ticker_id", "?")),
            "direction": sig.get("direction", "Long"),
            "confidence": sig.get("confidence", 0),
            "strategy": sig.get("strategy", "?"),
            "hurst_regime": sig.get("hurst_regime", "?"),
            "adx": round(sig.get("adx", 0), 1) if sig.get("adx") else None,
            "rvol": round(sig.get("rvol", 0), 2) if sig.get("rvol") else None,
            "structural_score": sig.get("structural_score"),
        }
        # Remove None values for compactness
        entry = {k: v for k, v in entry.items() if v is not None}
        parts.append(json.dumps(entry))

    parts.append("\n\nEvaluate all signals. Return pure JSON array.")
    return "\n".join(parts)


_DAILY_PLAN_SYSTEM = """You are the AEGIS V2 morning pre-market intelligence curator for an autonomous UK ISA leveraged ETP trading engine.

ROLE: Review the ticker universe, recent performance, and market regime.
Recommend which tickers to focus on and how to weight strategies for today.

OUTPUT FORMAT (pure JSON, no markdown wrapping):
{
  "date": "YYYY-MM-DD",
  "status": "complete",
  "regime_assessment": "<current regime and why>",
  "focus_tickers": ["<top 5-10 tickers to watch today>"],
  "avoid_tickers": ["<tickers to avoid and why>"],
  "strategy_weights": {
    "F_MOM": 0.0-1.0,
    "F_REV": 0.0-1.0,
    "F_MAC": 0.0-1.0,
    "F_DIS": 0.0-1.0
  },
  "risk_notes": ["<risk items to watch>"],
  "confidence": 0.0-1.0
}

DECISION CRITERIA:
- In trending regimes (Hurst > 0.55): weight F_MOM higher, F_REV lower.
- In mean-reverting (Hurst < 0.45): weight F_REV higher, F_MOM lower.
- Random walk: balanced weights, reduce overall sizing.
- After consecutive losses: conservative weights, reduce F_DIS.
- After strong performance: maintain, don't get aggressive.
- Avoid tickers with Wilson-blacklist WR or recent L1 (spread victim) pattern.

Be concise. Be mathematical."""


def _build_daily_plan_prompt(
    ticker_scores: dict,
    market_regime: str,
    recent_performance: dict,
) -> str:
    """Build the prompt for daily plan curation."""
    parts = [_DAILY_PLAN_SYSTEM, "\n\n--- TICKER SCORES (top 20 by composite) ---"]

    # Sort and take top 20 to keep prompt compact
    sorted_tickers = sorted(ticker_scores.items(), key=lambda x: x[1], reverse=True)[:20]
    for ticker, score in sorted_tickers:
        parts.append(f"  {ticker}: {score:.1f}")

    parts.append(f"\n--- MARKET REGIME ---\n{market_regime}")

    if recent_performance:
        perf_summary = {}
        for key in ("total_trades", "win_rate", "profit_factor", "avg_win", "avg_loss",
                    "consecutive_losses", "daily_pnl", "weekly_pnl", "drawdown_pct",
                    "equity", "best_strategy", "worst_strategy", "regime"):
            val = recent_performance.get(key)
            if val is not None:
                perf_summary[key] = round(val, 4) if isinstance(val, float) else val
        if perf_summary:
            parts.append("\n--- RECENT PERFORMANCE (7 days) ---")
            parts.append(json.dumps(perf_summary, indent=2))

    parts.append(f"\n\nGenerate today's plan for {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. Return pure JSON.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def evaluate_signal(
    signal_dict: dict,
    market_context: Optional[dict] = None,
) -> dict:
    """Evaluate a single trade signal before execution.

    Takes a signal from bridge.py and asks Claude to approve/reject/modify it.
    Designed for live mode with <5s latency target.

    Args:
        signal_dict: Signal from bridge.py with keys:
            ticker_id, direction, confidence, kelly_fraction, shares,
            strategy, rvol, hurst, hurst_regime, adx, vol_slope,
            structural_score, vwap_dist_pct, volume_divergence
        market_context: Optional dict with keys:
            regime, vix, spy_return, session, exchange, drawdown_pct,
            equity, open_positions, trades_today, consecutive_losses

    Returns:
        Dict with keys:
            claude_verdict: "approve" | "reject" | "reduce"
            adjusted_confidence: int (0-100)
            adjusted_kelly: float (0.0-0.35)
            reasoning: str
            risk_flags: list[str]
            latency_ms: float
            cached: bool
            simulation: bool (True if SIMULATION_MODE)
    """
    if market_context is None:
        market_context = {}

    # Simulation mode bypass
    if SIMULATION_MODE:
        result = _sim_evaluate_signal(signal_dict, market_context)
        _log_interaction("evaluate_signal", 0, result, 0.0)
        return result

    # Check cache
    ck = _cache_key(signal_dict, market_context)
    cached = _cache_get(ck)
    if cached is not None:
        cached["cached"] = True
        _log_interaction("evaluate_signal", 0, cached, 0.0, cached=True)
        return cached

    # Build prompt and call Claude
    prompt = _build_signal_prompt(signal_dict, market_context)
    t0 = time.monotonic()
    raw = _call_claude(prompt, timeout=TIMEOUT_SINGLE)
    latency_ms = (time.monotonic() - t0) * 1000

    if raw is None:
        # Claude unavailable — fail open with conservative haircut
        fallback = _fallback_evaluate(signal_dict)
        fallback["latency_ms"] = latency_ms
        _log_interaction("evaluate_signal", len(prompt), fallback, latency_ms, error="claude_unavailable")
        return fallback

    # Parse response
    parsed = _parse_json_from_text(raw)
    if parsed is None:
        # Parse failure — attempt keyword extraction from raw text
        parsed = _keyword_extract_verdict(raw, signal_dict)

    result = _normalize_verdict(parsed, signal_dict, latency_ms)
    _cache_put(ck, result)
    _log_interaction("evaluate_signal", len(prompt), result, latency_ms)
    return result


def post_trade_analysis(trade_result_dict: dict) -> dict:
    """Analyze a completed trade for lessons and pattern recognition.

    Args:
        trade_result_dict: Completed trade with keys:
            ticker, symbol, direction, strategy, confidence, kelly_fraction,
            entry_price, exit_price, pnl, pnl_pct, duration_secs,
            rungs_achieved, max_rung, mae, mfe, mae_pct, mfe_pct,
            spread_at_entry, spread_at_exit, entry_time, exit_time,
            exit_reason, regime_at_entry, vix_at_entry, entry_indicators

    Returns:
        Dict with keys:
            classification: str (W1-W5 or L1-L7)
            narrative: str
            lessons: list[str]
            pattern: str
            root_cause: str
            suggested_adjustments: list[dict]
            mfe_utilization: str
            confidence: float
            simulation: bool
    """
    if SIMULATION_MODE:
        result = _sim_post_trade(trade_result_dict)
        _log_interaction("post_trade_analysis", 0, result, 0.0)
        return result

    prompt = _build_post_trade_prompt(trade_result_dict)
    t0 = time.monotonic()
    raw = _call_claude(prompt, timeout=TIMEOUT_ANALYSIS)
    latency_ms = (time.monotonic() - t0) * 1000

    if raw is None:
        fallback = _fallback_post_trade(trade_result_dict)
        fallback["latency_ms"] = latency_ms
        _log_interaction("post_trade_analysis", len(prompt), fallback, latency_ms, error="claude_unavailable")
        return fallback

    parsed = _parse_json_from_text(raw)
    if parsed is None:
        parsed = _keyword_extract_classification(raw, trade_result_dict)

    result = _normalize_post_trade(parsed, trade_result_dict, latency_ms)
    _log_interaction("post_trade_analysis", len(prompt), result, latency_ms)
    return result


def batch_evaluate(signals_list: list) -> list:
    """Evaluate many signals at once for backtesting.

    Batches signals into a single Claude prompt for efficiency.
    Can process 100+ signals per call.

    Args:
        signals_list: List of signal dicts (same format as evaluate_signal).
            Each may optionally include a "_context" key with market context.

    Returns:
        List of verdict dicts (same format as evaluate_signal return).
        One per input signal, in the same order.
    """
    if not signals_list:
        return []

    if SIMULATION_MODE:
        results = _sim_batch_evaluate(signals_list)
        _log_interaction("batch_evaluate", 0, {"count": len(results)}, 0.0)
        return results

    # Split into chunks of 50 to keep prompts manageable
    chunk_size = 50
    all_results = []

    for chunk_start in range(0, len(signals_list), chunk_size):
        chunk = signals_list[chunk_start:chunk_start + chunk_size]
        prompt = _build_batch_prompt(chunk)

        t0 = time.monotonic()
        raw = _call_claude(prompt, timeout=TIMEOUT_BATCH)
        latency_ms = (time.monotonic() - t0) * 1000

        if raw is None:
            # Fallback for entire chunk
            for sig in chunk:
                all_results.append(_fallback_evaluate(sig))
            _log_interaction("batch_evaluate", len(prompt), {"count": len(chunk), "fallback": True}, latency_ms, error="claude_unavailable")
            continue

        parsed = _parse_json_from_text(raw)

        if isinstance(parsed, list):
            # Good — got array of verdicts
            for i, sig in enumerate(chunk):
                if i < len(parsed):
                    entry = parsed[i]
                    verdict = {
                        "claude_verdict": entry.get("claude_verdict", "approve"),
                        "adjusted_confidence": entry.get("adjusted_confidence", sig.get("confidence", 50)),
                        "reasoning": entry.get("reasoning", ""),
                        "risk_flags": entry.get("risk_flags", []),
                        "latency_ms": latency_ms / len(chunk),
                        "cached": False,
                        "simulation": False,
                    }
                    # Compute adjusted kelly from confidence ratio
                    orig_conf = sig.get("confidence", 50)
                    adj_conf = verdict["adjusted_confidence"]
                    orig_kelly = sig.get("kelly_fraction", 0.10)
                    ratio = adj_conf / max(orig_conf, 1)
                    verdict["adjusted_kelly"] = round(min(orig_kelly * ratio, 0.35), 4)
                    all_results.append(verdict)
                else:
                    all_results.append(_fallback_evaluate(sig))
        else:
            # Parse failure — fallback for chunk
            for sig in chunk:
                all_results.append(_fallback_evaluate(sig))
            _log_interaction("batch_evaluate", len(prompt), {"count": len(chunk), "parse_error": True}, latency_ms, error="json_parse_failure")

        _log_interaction("batch_evaluate", len(prompt), {"count": len(chunk)}, latency_ms)

    return all_results


def curate_daily_plan(
    ticker_scores: dict,
    market_regime: str,
    recent_performance: Optional[dict] = None,
) -> dict:
    """Generate a morning pre-market analysis and daily plan.

    Args:
        ticker_scores: Dict of {ticker_symbol: composite_score}.
        market_regime: Current regime string (e.g., "trending", "mean_reverting", "random").
        recent_performance: Optional dict with recent performance metrics:
            total_trades, win_rate, profit_factor, avg_win, avg_loss,
            consecutive_losses, daily_pnl, weekly_pnl, drawdown_pct, equity

    Returns:
        Dict with keys:
            date: str
            status: str
            regime_assessment: str
            focus_tickers: list[str]
            avoid_tickers: list[str]
            strategy_weights: dict
            risk_notes: list[str]
            confidence: float
            simulation: bool
    """
    if recent_performance is None:
        recent_performance = {}

    if SIMULATION_MODE:
        result = _sim_daily_plan(ticker_scores, market_regime, recent_performance)
        _log_interaction("curate_daily_plan", 0, result, 0.0)
        return result

    prompt = _build_daily_plan_prompt(ticker_scores, market_regime, recent_performance)
    t0 = time.monotonic()
    raw = _call_claude(prompt, timeout=TIMEOUT_ANALYSIS)
    latency_ms = (time.monotonic() - t0) * 1000

    if raw is None:
        fallback = _sim_daily_plan(ticker_scores, market_regime, recent_performance)
        fallback["status"] = "fallback_no_claude"
        fallback["latency_ms"] = latency_ms
        _log_interaction("curate_daily_plan", len(prompt), fallback, latency_ms, error="claude_unavailable")
        return fallback

    parsed = _parse_json_from_text(raw)
    if parsed is None:
        fallback = _sim_daily_plan(ticker_scores, market_regime, recent_performance)
        fallback["status"] = "fallback_parse_error"
        fallback["latency_ms"] = latency_ms
        _log_interaction("curate_daily_plan", len(prompt), fallback, latency_ms, error="json_parse_failure")
        return fallback

    # Normalize and fill defaults
    result = {
        "date": parsed.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "status": parsed.get("status", "complete"),
        "regime_assessment": parsed.get("regime_assessment", f"Regime: {market_regime}"),
        "focus_tickers": parsed.get("focus_tickers", []),
        "avoid_tickers": parsed.get("avoid_tickers", []),
        "strategy_weights": parsed.get("strategy_weights", {"F_MOM": 0.4, "F_REV": 0.3, "F_MAC": 0.2, "F_DIS": 0.1}),
        "risk_notes": parsed.get("risk_notes", []),
        "confidence": parsed.get("confidence", 0.5),
        "latency_ms": latency_ms,
        "simulation": False,
    }
    _log_interaction("curate_daily_plan", len(prompt), result, latency_ms)
    return result


# ---------------------------------------------------------------------------
# Fallback / keyword extraction for when JSON parsing fails
# ---------------------------------------------------------------------------
def _fallback_evaluate(signal: dict) -> dict:
    """Conservative fallback when Claude is unavailable.

    Applies a 10% confidence haircut — better to be slightly conservative
    than to either block everything or let everything through unchanged.
    """
    orig_conf = signal.get("confidence", 50)
    orig_kelly = signal.get("kelly_fraction", 0.10)

    # Conservative haircut: reduce confidence by 10%, floor at 0
    adj_conf = max(int(orig_conf * 0.90), 0)
    adj_kelly = round(orig_kelly * 0.90, 4)

    return {
        "claude_verdict": "approve",  # Fail open — don't block signals without analysis
        "adjusted_confidence": adj_conf,
        "adjusted_kelly": adj_kelly,
        "reasoning": "Claude unavailable — applying 10% conservative haircut.",
        "risk_flags": ["claude_unavailable"],
        "latency_ms": 0.0,
        "cached": False,
        "simulation": False,
        "fallback": True,
    }


def _fallback_post_trade(trade: dict) -> dict:
    """Fallback post-trade when Claude is unavailable."""
    pnl = trade.get("pnl", 0)
    return {
        "classification": "W1" if pnl > 0 else "L1" if pnl < 0 else "BREAKEVEN",
        "narrative": "Claude unavailable — automatic classification based on PnL sign only.",
        "lessons": [],
        "pattern": "unknown",
        "root_cause": "analysis_unavailable",
        "suggested_adjustments": [],
        "mfe_utilization": "unknown",
        "confidence": 0.0,
        "latency_ms": 0.0,
        "simulation": False,
        "fallback": True,
    }


def _keyword_extract_verdict(raw_text: str, signal: dict) -> dict:
    """Extract verdict from raw text using keyword matching when JSON parse fails."""
    text_lower = raw_text.lower()

    # Determine verdict from keywords
    if "reject" in text_lower:
        verdict = "reject"
    elif "reduce" in text_lower or "lower" in text_lower or "haircut" in text_lower:
        verdict = "reduce"
    else:
        verdict = "approve"

    # Try to extract confidence number
    adj_conf = signal.get("confidence", 50)
    import re
    conf_match = re.search(r"(?:confidence|adjusted)[:\s]*(\d{1,3})", text_lower)
    if conf_match:
        extracted = int(conf_match.group(1))
        if 0 <= extracted <= 100:
            adj_conf = extracted

    return {
        "claude_verdict": verdict,
        "adjusted_confidence": adj_conf,
        "reasoning": raw_text[:300] if raw_text else "No response parsed",
        "risk_flags": ["keyword_extraction_fallback"],
    }


def _keyword_extract_classification(raw_text: str, trade: dict) -> dict:
    """Extract trade classification from raw text using keyword matching."""
    import re
    text_upper = raw_text.upper()

    # Match W1-W5 or L1-L7
    class_match = re.search(r"\b([WL][1-7])\b", text_upper)
    classification = class_match.group(1) if class_match else ("W1" if trade.get("pnl", 0) > 0 else "L1")

    return {
        "classification": classification,
        "narrative": raw_text[:500] if raw_text else "No response parsed",
        "lessons": [],
        "pattern": "unknown",
        "root_cause": "keyword_extraction",
        "suggested_adjustments": [],
    }


def _normalize_verdict(parsed: dict, signal: dict, latency_ms: float) -> dict:
    """Normalize a parsed verdict dict to the expected output schema."""
    if not parsed:
        return _fallback_evaluate(signal)

    verdict = str(parsed.get("claude_verdict", "approve")).lower()
    if verdict not in ("approve", "reject", "reduce"):
        verdict = "approve"

    orig_conf = signal.get("confidence", 50)
    adj_conf = parsed.get("adjusted_confidence", orig_conf)
    if not isinstance(adj_conf, (int, float)):
        adj_conf = orig_conf
    adj_conf = max(0, min(100, int(adj_conf)))

    orig_kelly = signal.get("kelly_fraction", 0.10)
    adj_kelly = parsed.get("adjusted_kelly")
    if adj_kelly is None or not isinstance(adj_kelly, (int, float)):
        # Derive from confidence ratio
        ratio = adj_conf / max(orig_conf, 1)
        adj_kelly = round(min(orig_kelly * ratio, 0.35), 4)
    else:
        adj_kelly = round(max(0.0, min(0.35, float(adj_kelly))), 4)

    return {
        "claude_verdict": verdict,
        "adjusted_confidence": adj_conf,
        "adjusted_kelly": adj_kelly,
        "reasoning": str(parsed.get("reasoning", ""))[:500],
        "risk_flags": parsed.get("risk_flags", []),
        "pattern_match": parsed.get("pattern_match", ""),
        "latency_ms": latency_ms,
        "cached": False,
        "simulation": False,
    }


def _normalize_post_trade(parsed: dict, trade: dict, latency_ms: float) -> dict:
    """Normalize a parsed post-trade analysis to the expected output schema."""
    if not parsed:
        return _fallback_post_trade(trade)

    # Validate classification
    classification = str(parsed.get("classification", "")).upper()
    import re
    if not re.match(r"^[WL][1-7]$", classification):
        pnl = trade.get("pnl", 0)
        classification = "W1" if pnl > 0 else "L1" if pnl < 0 else "BREAKEVEN"

    return {
        "classification": classification,
        "narrative": str(parsed.get("narrative", ""))[:500],
        "lessons": parsed.get("lessons", []),
        "pattern": str(parsed.get("pattern", "unknown")),
        "root_cause": str(parsed.get("root_cause", "unknown")),
        "suggested_adjustments": parsed.get("suggested_adjustments", []),
        "mfe_utilization": str(parsed.get("mfe_utilization", "unknown")),
        "confidence": max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
        "latency_ms": latency_ms,
        "simulation": False,
    }


# ---------------------------------------------------------------------------
# CLI entry point for testing
# ---------------------------------------------------------------------------
def _test_cli():
    """Simple CLI test harness."""
    import argparse
    parser = argparse.ArgumentParser(description="Claude Curator test harness")
    parser.add_argument("--sim", action="store_true", help="Force simulation mode")
    parser.add_argument("--signal", action="store_true", help="Test signal evaluation")
    parser.add_argument("--trade", action="store_true", help="Test post-trade analysis")
    parser.add_argument("--batch", action="store_true", help="Test batch evaluation")
    parser.add_argument("--plan", action="store_true", help="Test daily plan curation")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.sim:
        global SIMULATION_MODE
        SIMULATION_MODE = True
        log.info("Forced SIMULATION_MODE=True")

    test_signal = {
        "ticker_id": 42,
        "symbol": "NVD3.L",
        "direction": "Long",
        "confidence": 72,
        "kelly_fraction": 0.15,
        "shares": 100,
        "strategy": "TypeB",
        "rvol": 2.3,
        "hurst": 0.62,
        "hurst_regime": "trending",
        "adx": 28.5,
        "vol_slope": 0.15,
        "volume_divergence": 0.05,
        "vwap_dist_pct": 0.3,
        "structural_score": 68,
    }

    test_context = {
        "regime": "trending",
        "vix": 18.5,
        "spy_return": 0.3,
        "session": "LSE_MAIN",
        "exchange": "LSE",
        "drawdown_pct": 0.01,
        "equity": 10000.0,
        "open_positions": 1,
        "trades_today": 2,
        "consecutive_losses": 0,
    }

    test_trade = {
        "ticker": 42,
        "symbol": "NVD3.L",
        "direction": "Long",
        "strategy": "TypeB",
        "confidence": 72,
        "kelly_fraction": 0.15,
        "entry_price": 150.50,
        "exit_price": 153.20,
        "pnl": 2.70,
        "pnl_pct": 1.79,
        "duration_secs": 1800,
        "rungs_achieved": 3,
        "max_rung": 3,
        "mae": -0.80,
        "mfe": 3.10,
        "mae_pct": -0.53,
        "mfe_pct": 2.06,
        "spread_at_entry": 0.15,
        "exit_reason": "chandelier_rung_3",
        "regime_at_entry": "trending",
        "entry_indicators": {
            "rvol": 2.3,
            "hurst": 0.62,
            "adx": 28.5,
        },
    }

    if args.signal or (not args.trade and not args.batch and not args.plan):
        log.info("=== Testing evaluate_signal ===")
        result = evaluate_signal(test_signal, test_context)
        print(json.dumps(result, indent=2))

    if args.trade:
        log.info("=== Testing post_trade_analysis ===")
        result = post_trade_analysis(test_trade)
        print(json.dumps(result, indent=2))

    if args.batch:
        log.info("=== Testing batch_evaluate ===")
        signals = [test_signal] * 5
        results = batch_evaluate(signals)
        print(json.dumps(results, indent=2))

    if args.plan:
        log.info("=== Testing curate_daily_plan ===")
        scores = {"NVD3.L": 85.0, "QQQ3.L": 78.0, "3LUS.L": 72.0, "AAPL": 65.0}
        result = curate_daily_plan(scores, "trending", {"win_rate": 0.58, "total_trades": 45})
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _test_cli()
