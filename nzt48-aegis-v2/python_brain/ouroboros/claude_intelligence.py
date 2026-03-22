"""Claude Intelligence Layer -- core integration module for AEGIS V2.

Provides the `claude_analyze()` function used by all Claude intelligence roles
(forensic review, backtest analyst, gate calibration, etc.). Wraps the Claude
Code CLI (`claude -p`) with retry, timeout, and structured output parsing.

Architecture:
    - Claude operates exclusively on the COLD PATH (nightly, 2-hourly, weekly).
    - Zero involvement in the hot path (tick processing, stop trailing, orders).
    - All calls go through subprocess (`claude -p`) -- NOT a resident daemon.
    - Cost: $0/month via Max subscription on EC2.

Usage:
    from python_brain.ouroboros.claude_intelligence import claude_analyze

    result = claude_analyze(
        prompt="Classify these trades...",
        context="WAL data here...",
        max_tokens=4096,
    )
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("claude_intelligence")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLAUDE_CMD = ["claude", "-p"]
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 120  # seconds per call
MAX_RETRIES = 3
BACKOFF_BASE = 2  # exponential backoff base (2^attempt seconds)
MAX_CONTEXT_CHARS = 80_000  # safety cap to avoid CLI argument overflow

CLAUDE_MD_PATH = Path(os.environ.get("AEGIS_CLAUDE_MD", "/app/CLAUDE.md"))


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------
def claude_analyze(
    prompt: str,
    context: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT,
    expect_json: bool = True,
) -> str:
    """Call Claude Code CLI and return the response text.

    This is the primary entry point for all Claude intelligence modules.
    Uses subprocess to call `claude -p` with the Max subscription ($0/call).

    Args:
        prompt: The analysis prompt (what to do).
        context: Supporting data (WAL events, metrics, etc.).
        max_tokens: Maximum response tokens (default 4096).
        timeout: Seconds before subprocess is killed (default 120).
        expect_json: If True, attempts to extract JSON from the response.

    Returns:
        Raw response text from Claude. Caller is responsible for parsing.
        Returns empty string on all-retries failure.
    """
    # Load CLAUDE.md project context if available
    system_context = _load_claude_md()

    # Build full prompt: system context + data context + analysis prompt
    parts = []
    if system_context:
        parts.append(system_context)
    if context:
        # Truncate context to prevent CLI argument overflow
        truncated = context[:MAX_CONTEXT_CHARS]
        if len(context) > MAX_CONTEXT_CHARS:
            truncated += f"\n... (truncated from {len(context)} to {MAX_CONTEXT_CHARS} chars)"
            log.warning("Context truncated: %d -> %d chars", len(context), MAX_CONTEXT_CHARS)
        parts.append(truncated)
    parts.append(prompt)
    full_prompt = "\n\n".join(parts)

    # Build command -- pipe prompt via stdin to avoid shell argument length limits
    cmd = CLAUDE_CMD + ["--output-format", "json", "--max-tokens", str(max_tokens)]

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            log.info(
                "Claude CLI call (attempt %d/%d, timeout=%ds, prompt=%d chars)",
                attempt + 1, MAX_RETRIES, timeout, len(full_prompt),
            )
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.environ.get("AEGIS_ROOT", "/app"),
            )

            if result.returncode != 0:
                last_error = result.stderr[:500] if result.stderr else f"exit code {result.returncode}"
                log.warning(
                    "Claude CLI error (attempt %d/%d): %s",
                    attempt + 1, MAX_RETRIES, last_error,
                )
                if attempt < MAX_RETRIES - 1:
                    _backoff(attempt)
                continue

            output = result.stdout.strip()
            if not output:
                last_error = "empty stdout"
                log.warning("Claude CLI returned empty output (attempt %d/%d)", attempt + 1, MAX_RETRIES)
                if attempt < MAX_RETRIES - 1:
                    _backoff(attempt)
                continue

            # Extract content from Claude CLI JSON envelope
            extracted = _extract_content(output)
            if extracted:
                log.info(
                    "Claude CLI success (attempt %d/%d, response=%d chars)",
                    attempt + 1, MAX_RETRIES, len(extracted),
                )
                return extracted

            # Fallback: return raw output if extraction failed
            log.warning("Content extraction failed, returning raw output")
            return output

        except subprocess.TimeoutExpired:
            last_error = f"timeout after {timeout}s"
            log.error(
                "Claude CLI timeout (%ds, attempt %d/%d)",
                timeout, attempt + 1, MAX_RETRIES,
            )
        except OSError as e:
            last_error = str(e)
            log.error("Claude CLI OS error: %s", e)
        except Exception as e:
            last_error = str(e)
            log.error("Claude CLI unexpected error: %s", e)

        if attempt < MAX_RETRIES - 1:
            _backoff(attempt)

    log.error("Claude CLI: all %d retries exhausted. Last error: %s", MAX_RETRIES, last_error)
    return ""


def claude_analyze_json(
    prompt: str,
    context: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[Dict[str, Any]]:
    """Call Claude and parse the response as JSON.

    Convenience wrapper around claude_analyze() that handles JSON extraction
    from the response, including markdown-wrapped JSON blocks.

    Returns:
        Parsed JSON dict, or None on failure.
    """
    raw = claude_analyze(prompt, context, max_tokens, timeout, expect_json=True)
    if not raw:
        return None
    return _parse_json_response(raw)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _backoff(attempt: int) -> None:
    """Exponential backoff between retries."""
    delay = BACKOFF_BASE ** attempt
    log.info("Backing off %ds before retry", delay)
    time.sleep(delay)


def _load_claude_md() -> str:
    """Load CLAUDE.md project context for system-level instructions."""
    if CLAUDE_MD_PATH.exists():
        try:
            return CLAUDE_MD_PATH.read_text(errors="replace")
        except OSError:
            pass
    return ""


def _extract_content(raw_output: str) -> str:
    """Extract the actual response content from Claude CLI JSON envelope.

    Claude CLI with --output-format json wraps responses in:
        {"type": "result", "result": "...actual content..."}

    This function unwraps that envelope and returns the inner content.
    """
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        # Not valid JSON -- return as-is (might be plain text)
        return raw_output

    if not isinstance(parsed, dict):
        return raw_output

    # Claude CLI envelope: {"type": "result", "result": "..."}
    if parsed.get("type") == "result" and "result" in parsed:
        inner = parsed["result"]
        if isinstance(inner, str):
            return inner.strip()
        elif isinstance(inner, dict):
            return json.dumps(inner)
        else:
            return str(inner)

    # No envelope -- return as-is
    return raw_output


def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from Claude's response, handling markdown code blocks."""
    cleaned = text.strip()

    # Strip markdown JSON fences
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
        if "```" in cleaned:
            cleaned = cleaned[:cleaned.rfind("```")].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()
        if "```" in cleaned:
            cleaned = cleaned[:cleaned.rfind("```")].strip()

    # Direct parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text (Claude sometimes adds preamble)
    brace_start = cleaned.find("{")
    if brace_start >= 0:
        # Find matching closing brace
        depth = 0
        for i in range(brace_start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[brace_start:i + 1])
                    except json.JSONDecodeError:
                        break

    log.warning("Failed to parse JSON from Claude response (%d chars)", len(text))
    return None
