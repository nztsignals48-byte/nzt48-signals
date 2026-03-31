"""
Sprint S04+S08: Shared utilities for all Claude intelligence modules.

HYBRID ARCHITECTURE:
    1. Claude CLI (primary) — uses Max subscription ($0/call, Opus 4.6)
    2. Anthropic API (fallback) — uses ANTHROPIC_API_KEY ($X/call, model-tiered)
    3. Gemini API (last resort) — uses GEMINI_API_KEY ($X/call, gemini-2.5-flash)

Model tiering for API fallback (cost optimization):
    - Haiku:  Live signal evaluation (fast, cheap — $0.25/MTok)
    - Sonnet: Briefings, filing scans, psych audit (balanced)
    - Opus:   Nightly review, decisions, hypotheses (deep reasoning)

The CLI always uses Opus 4.6 via Max subscription regardless of model param.
Model param only affects API fallback routing.
"""

import json
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLAUDE_CMD = ["claude", "-p"]
MAX_RETRIES = 3
TIMEOUT_SECONDS = 300
MAX_CONTEXT_CHARS = 50000

# Model tier constants (for API fallback only — CLI always uses Opus via Max)
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-5-20250929"
MODEL_OPUS = "claude-opus-4-6"

# Default model for API fallback when no tier specified
DEFAULT_API_MODEL = MODEL_SONNET

# Track which backend was used (for logging/audit)
_last_backend = "none"


def get_last_backend() -> str:
    """Return which backend was used for the last query ('cli', 'api', 'gemini', 'none')."""
    return _last_backend


# ---------------------------------------------------------------------------
# Anthropic API client (lazy init)
# ---------------------------------------------------------------------------
_anthropic_client = None
_anthropic_available = None  # None = not checked yet


def _get_anthropic_client():
    """Lazy-init Anthropic client. Returns None if SDK or key unavailable."""
    global _anthropic_client, _anthropic_available

    if _anthropic_available is False:
        return None
    if _anthropic_client is not None:
        return _anthropic_client

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.info("ANTHROPIC_API_KEY not set — API fallback disabled")
        _anthropic_available = False
        return None

    try:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        _anthropic_available = True
        log.info("Anthropic API client initialized (fallback ready)")
        return _anthropic_client
    except ImportError:
        log.info("anthropic SDK not installed — API fallback disabled")
        _anthropic_available = False
        return None
    except Exception as e:
        log.warning("Anthropic client init failed: %s", e)
        _anthropic_available = False
        return None


# ---------------------------------------------------------------------------
# Core hybrid query function
# ---------------------------------------------------------------------------
def claude_query(
    prompt: str,
    system_context: str = "",
    max_retries: int = MAX_RETRIES,
    model: str = "",
    timeout: int = TIMEOUT_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Call Claude and return parsed JSON response.

    HYBRID: Tries CLI first (free via Max), falls back to Anthropic API,
    then Gemini API. Model param only affects API fallback — CLI always
    uses Opus 4.6 via Max subscription.

    Args:
        prompt: The analysis prompt.
        system_context: Optional system context prepended to prompt.
        max_retries: Retries per backend.
        model: API model tier (MODEL_HAIKU/SONNET/OPUS). Ignored for CLI.
        timeout: Seconds before timeout.

    Returns:
        Parsed JSON dict, or None on all-backends failure.
    """
    global _last_backend

    full_prompt = prompt
    if system_context:
        full_prompt = system_context + "\n\n" + prompt

    # 1. Try Claude CLI (free via Max subscription)
    result = _try_cli(full_prompt, max_retries, timeout)
    if result is not None:
        _last_backend = "cli"
        return result

    # 2. Try Anthropic API (paid, model-tiered)
    api_model = model or DEFAULT_API_MODEL
    result = _try_anthropic_api(full_prompt, system_context, api_model, max_retries, timeout)
    if result is not None:
        _last_backend = "api"
        return result

    # 3. Try Gemini API (last resort)
    result = _try_gemini(full_prompt, max_retries)
    if result is not None:
        _last_backend = "gemini"
        return result

    _last_backend = "none"
    log.error("All backends failed (CLI + API + Gemini)")
    return None


# ---------------------------------------------------------------------------
# Backend 1: Claude CLI (Max subscription, $0/call)
# ---------------------------------------------------------------------------
def _try_cli(
    full_prompt: str,
    max_retries: int,
    timeout: int,
) -> Optional[Dict[str, Any]]:
    """Try Claude CLI. Returns parsed dict or None."""
    cmd = CLAUDE_CMD + [full_prompt, "--output-format", "json"]

    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd="/app",
            )
            if result.returncode != 0:
                log.warning(
                    "Claude CLI error (attempt %d/%d): %s",
                    attempt + 1, max_retries, result.stderr[:500],
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            output = result.stdout.strip()
            parsed = json.loads(output)

            # Unwrap CLI JSON envelope: {"type":"result","result":"..."}
            if isinstance(parsed, dict) and parsed.get("type") == "result" and "result" in parsed:
                inner = parsed["result"]
                if isinstance(inner, str):
                    inner = inner.strip()
                    # Strip markdown JSON fences
                    if inner.startswith("```json"):
                        inner = inner.split("```json", 1)[1]
                        if "```" in inner:
                            inner = inner.split("```", 1)[0]
                        inner = inner.strip()
                    elif inner.startswith("```"):
                        inner = inner.split("```", 1)[1]
                        if "```" in inner:
                            inner = inner.split("```", 1)[0]
                        inner = inner.strip()
                    try:
                        return json.loads(inner)
                    except json.JSONDecodeError:
                        return {"text": inner, "raw": True}
                elif isinstance(inner, dict):
                    return inner
                else:
                    return {"text": str(inner), "raw": True}

            return parsed

        except subprocess.TimeoutExpired:
            log.error(
                "Claude CLI timeout (%ds, attempt %d/%d)",
                timeout, attempt + 1, max_retries,
            )
        except json.JSONDecodeError as e:
            log.error("Claude JSON parse error: %s — raw output: %s", e, output[:200])
        except FileNotFoundError:
            log.info("Claude CLI binary not found — skipping CLI backend")
            return None
        except Exception as e:
            log.error("Claude CLI unexpected error: %s", e)

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    log.warning("Claude CLI: all %d retries exhausted — trying API fallback", max_retries)
    return None


# ---------------------------------------------------------------------------
# Backend 2: Anthropic API (paid, model-tiered)
# ---------------------------------------------------------------------------
def _try_anthropic_api(
    full_prompt: str,
    system_context: str,
    model: str,
    max_retries: int,
    timeout: int,
) -> Optional[Dict[str, Any]]:
    """Try Anthropic API. Returns parsed dict or None."""
    client = _get_anthropic_client()
    if client is None:
        return None

    for attempt in range(max_retries):
        try:
            log.info(
                "Anthropic API call (model=%s, attempt %d/%d, prompt=%d chars)",
                model, attempt + 1, max_retries, len(full_prompt),
            )

            # Build messages — separate system from user content
            messages = [{"role": "user", "content": full_prompt}]

            kwargs = {
                "model": model,
                "max_tokens": 4096,
                "messages": messages,
            }
            if system_context:
                kwargs["system"] = system_context

            response = client.messages.create(**kwargs)

            # Extract text from response
            raw = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw += block.text

            if not raw.strip():
                log.warning("Anthropic API returned empty response (attempt %d/%d)", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            # Log usage for cost tracking
            usage = response.usage
            log.info(
                "Anthropic API success: model=%s, input=%d, output=%d tokens",
                model, usage.input_tokens, usage.output_tokens,
            )

            # Parse JSON from response
            return _parse_json_response(raw.strip())

        except Exception as e:
            log.error("Anthropic API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    log.warning("Anthropic API: all %d retries exhausted", max_retries)
    return None


# ---------------------------------------------------------------------------
# Backend 3: Gemini API (last resort)
# ---------------------------------------------------------------------------
def _try_gemini(full_prompt: str, max_retries: int) -> Optional[Dict[str, Any]]:
    """Try Gemini API as last resort. Returns parsed dict or None."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log.info("GEMINI_API_KEY not set — Gemini fallback disabled")
        return None

    for attempt in range(max_retries):
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=4096,
                    temperature=0.2,
                ),
            )
            raw = response.text.strip()
            if raw:
                log.info("Gemini API success (attempt %d/%d)", attempt + 1, max_retries)
                return _parse_json_response(raw)

        except ImportError:
            log.info("google-generativeai SDK not installed — Gemini fallback disabled")
            return None
        except Exception as e:
            log.error("Gemini API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    log.warning("Gemini API: all %d retries exhausted", max_retries)
    return None


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------
def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from LLM response, handling markdown code blocks."""
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
        if isinstance(result, list):
            return {"items": result, "raw_list": True}
    except json.JSONDecodeError:
        pass

    # Find JSON object in text
    brace_start = cleaned.find("{")
    if brace_start >= 0:
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

    # Return raw text if no JSON found
    if text.strip():
        return {"text": text.strip(), "raw": True}
    return None


# ---------------------------------------------------------------------------
# Context file utilities
# ---------------------------------------------------------------------------
def load_context_files(
    file_paths: Optional[List[str]] = None,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> Dict[str, str]:
    """Load standard context files for Claude prompts.

    Returns dict of {name: content}. Truncates large files.
    """
    if file_paths is None:
        file_paths = [
            "/app/data/nightly_output.json",
            "/app/data/gate_vetoes.ndjson",
            "/app/config/dynamic_weights.toml",
            "/app/data/context_store.json",
            "/app/data/persistent_memory.json",
        ]

    context = {}
    for path_str in file_paths:
        p = Path(path_str)
        name = p.stem
        if p.exists():
            try:
                content = p.read_text(errors="replace")
                if len(content) > max_chars:
                    content = content[:max_chars] + "\n... (truncated)"
                    log.warning("Context file %s truncated to %d chars", path_str, max_chars)
                context[name] = content
            except Exception as e:
                context[name] = f"(read error: {e})"
                log.warning("Failed to read context file %s: %s", path_str, e)
        else:
            context[name] = "(not found)"
    return context


def build_context_string(context: Dict[str, str], max_total: int = MAX_CONTEXT_CHARS) -> str:
    """Convert context dict to a single string, respecting total char budget."""
    parts = []
    total = 0
    for name, content in context.items():
        header = f"\n=== {name} ===\n"
        remaining = max_total - total - len(header)
        if remaining <= 0:
            break
        truncated = content[:remaining]
        parts.append(header + truncated)
        total += len(header) + len(truncated)
    return "".join(parts)


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the operator via Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("Telegram: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message[:4096],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        log.warning("Telegram: HTTP %d — %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


def load_claude_md() -> str:
    """Load CLAUDE.md project context for system prompt."""
    claude_md = Path("/app/CLAUDE.md")
    if claude_md.exists():
        return claude_md.read_text(errors="replace")
    return ""


# ---------------------------------------------------------------------------
# Available API keys registry
# ---------------------------------------------------------------------------
def get_available_backends() -> Dict[str, bool]:
    """Return which backends are available for Claude queries.

    Checks:
    - CLI: whether `claude` binary exists on PATH
    - API: whether ANTHROPIC_API_KEY is set
    - Gemini: whether GEMINI_API_KEY is set
    """
    import shutil
    return {
        "cli": shutil.which("claude") is not None,
        "anthropic_api": bool(os.environ.get("ANTHROPIC_API_KEY", "")),
        "gemini_api": bool(os.environ.get("GEMINI_API_KEY", "")),
    }


def log_backend_status():
    """Log which backends are available. Call at startup for diagnostics."""
    backends = get_available_backends()
    available = [k for k, v in backends.items() if v]
    unavailable = [k for k, v in backends.items() if not v]
    log.info("Claude backends available: %s", ", ".join(available) or "NONE")
    if unavailable:
        log.info("Claude backends unavailable: %s", ", ".join(unavailable))
    if not available:
        log.warning("NO Claude backends available — all AI modules will use fallbacks")


# Auto-log backend status on first import (diagnostics)
try:
    log_backend_status()
except Exception:
    pass  # Don't crash on import if logging isn't configured yet
