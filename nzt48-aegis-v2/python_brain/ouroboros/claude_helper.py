"""
Sprint S04: Shared utilities for all Claude intelligence modules.
Uses Claude Code CLI via Max subscription ($0/month).
"""

import json
import os
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CLAUDE_CMD = ["claude", "-p"]
MAX_RETRIES = 3
TIMEOUT_SECONDS = 300
MAX_CONTEXT_CHARS = 50000


def claude_query(
    prompt: str,
    system_context: str = "",
    max_retries: int = MAX_RETRIES,
) -> Optional[Dict[str, Any]]:
    """Call Claude CLI and return parsed JSON response.

    Uses claude -p with Max subscription (Opus 4.6, $0/call).
    Retries up to max_retries times on failure with exponential backoff.

    Returns:
        Parsed JSON dict, or None on all-retries failure.
    """
    full_prompt = prompt
    if system_context:
        full_prompt = system_context + "\n\n" + prompt

    cmd = CLAUDE_CMD + [full_prompt, "--output-format", "json"]

    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
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

            # Handle markdown-wrapped JSON
            if output.startswith("```json"):
                output = output.split("```json", 1)[1]
                if "```" in output:
                    output = output.split("```", 1)[0]
                output = output.strip()
            elif output.startswith("```"):
                output = output.split("```", 1)[1]
                if "```" in output:
                    output = output.split("```", 1)[0]
                output = output.strip()

            return json.loads(output)

        except subprocess.TimeoutExpired:
            log.error(
                "Claude CLI timeout (%ds, attempt %d/%d)",
                TIMEOUT_SECONDS, attempt + 1, max_retries,
            )
        except json.JSONDecodeError as e:
            log.error("Claude JSON parse error: %s — raw output: %s", e, output[:200])
        except Exception as e:
            log.error("Claude CLI unexpected error: %s", e)

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    log.error("Claude CLI: all %d retries exhausted", max_retries)
    return None


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
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message[:4096],
            "parse_mode": parse_mode,
        }, timeout=10)
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
