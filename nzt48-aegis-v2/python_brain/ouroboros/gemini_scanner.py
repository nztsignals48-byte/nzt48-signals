"""
Gemini Scanner — LLM-powered universe curation for AEGIS V2.

Gemini scans the global market and recommends:
1. Core 80 tickers (2-hour rotation) — session-aware, highest-volume
2. Dark horse 20 tickers (15-min rotation) — unusual movers, volume spikes
3. Pre-market morning brief — what to watch today

Uses Google Gemini API (gemini-2.5-pro) via the google-generativeai SDK.
Falls back to deterministic scoring if API unavailable.

DOCTRINE:
    - Gemini operates exclusively on the COLD PATH (scheduled, not real-time).
    - Zero Positive Authority: Gemini recommends tickers for scanning, not for trading.
      It cannot force trades, override risk gates, or mutate live config.
    - The Rust 33-CHECK RiskArbiter is the final authority on all trade decisions.
    - Gemini output feeds into active_watchlist.json which the engine reads.

Architecture:
    - `scan_core_universe()` — 2-hourly: recommend 80 core tickers for streaming
    - `scan_dark_horses()` — 15-min: find 20 unusual movers for rotating slots
    - `morning_brief()` — daily 06:00 UTC: pre-market analysis and focus tickers
    - `_call_gemini()` — core API wrapper with retry, timeout, NDJSON logging

Usage:
    # As module
    from python_brain.ouroboros.gemini_scanner import scan_core_universe, scan_dark_horses

    core = scan_core_universe("EUROPEAN", all_contracts, market_data)
    dark = scan_dark_horses(all_contracts, market_data)

    # CLI
    python3 -m python_brain.ouroboros.gemini_scanner --core
    python3 -m python_brain.ouroboros.gemini_scanner --dark-horse
    python3 -m python_brain.ouroboros.gemini_scanner --brief
    python3 -m python_brain.ouroboros.gemini_scanner --test

Crontab entries (add to /app/crontab):
    # Gemini Core Universe Scan — every 2 hours Mon-Fri (aligned with ticker_selector)
    5 23 * * 0-4 cd /app && python3 -m python_brain.ouroboros.gemini_scanner --core >> /var/log/gemini_scanner.log 2>&1
    5 1,3,5,7,9,11,13,15,17,19,21 * * 1-5 cd /app && python3 -m python_brain.ouroboros.gemini_scanner --core >> /var/log/gemini_scanner.log 2>&1

    # Gemini Dark Horse Scan — every 15 min Mon-Fri during trading hours
    */15 0-21 * * 1-5 cd /app && python3 -m python_brain.ouroboros.gemini_scanner --dark-horse >> /var/log/gemini_scanner.log 2>&1

    # Gemini Morning Brief — 06:00 UTC Mon-Fri (before European session)
    0 6 * * 1-5 cd /app && python3 -m python_brain.ouroboros.gemini_scanner --brief >> /var/log/gemini_scanner.log 2>&1
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("gemini_scanner")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GEMINI_MODEL = "gemini-2.5-pro"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
TIMEOUT_CORE = 90         # seconds for core universe scan (Pro is slower than Flash)
TIMEOUT_DARK_HORSE = 60   # seconds for dark horse scan
TIMEOUT_BRIEF = 120       # seconds for morning brief (longer, richer output)
MAX_RETRIES = 3
BACKOFF_BASE = 2           # exponential backoff: 2^attempt seconds

# Scanner slot counts (mirrors config.toml [scanner])
CORE_SLOTS = 80
DARK_HORSE_SLOTS = 20

# Simulation mode — returns mock responses when Gemini API is unavailable
SIMULATION_MODE = os.environ.get("GEMINI_SCANNER_SIM", "0") == "1"

# Log file for all Gemini interactions (NDJSON)
LOG_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
LOG_FILE = LOG_DIR / "gemini_scanner.ndjson"

# Config and contract paths
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# ---------------------------------------------------------------------------
# Gemini SDK lazy import — graceful fallback if not installed
# ---------------------------------------------------------------------------
_genai = None
_genai_import_error: Optional[str] = None


def _ensure_genai():
    """Lazily import google.generativeai SDK. Returns True if available."""
    global _genai, _genai_import_error
    if _genai is not None:
        return True
    if _genai_import_error is not None:
        return False
    try:
        import google.generativeai as genai
        api_key = os.environ.get(GEMINI_API_KEY_ENV, "")
        if not api_key:
            _genai_import_error = f"Environment variable {GEMINI_API_KEY_ENV} not set"
            log.warning("Gemini SDK available but API key missing: %s", _genai_import_error)
            return False
        genai.configure(api_key=api_key)
        _genai = genai
        log.info("Gemini SDK configured (model=%s)", GEMINI_MODEL)
        return True
    except ImportError as e:
        _genai_import_error = f"google-generativeai not installed: {e}"
        log.warning("Gemini SDK not available: %s", _genai_import_error)
        return False
    except Exception as e:
        _genai_import_error = str(e)
        log.error("Gemini SDK configuration failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
_config_cache: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """Load scanner config from config.toml. Cached after first call."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    defaults = {
        "core_slots": CORE_SLOTS,
        "dark_horse_slots": DARK_HORSE_SLOTS,
        "dark_horse_min_rvol": 3.0,
        "dark_horse_min_gap_pct": 1.5,
        "dark_horse_min_volume_rank": 0.9,
        "session_allocation": {},
    }

    try:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                log.warning("No TOML parser available, using defaults")
                _config_cache = defaults
                return defaults

        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)
            scanner = data.get("scanner", {})
            defaults.update({
                "core_slots": scanner.get("core_slots", CORE_SLOTS),
                "dark_horse_slots": scanner.get("dark_horse_slots", DARK_HORSE_SLOTS),
                "dark_horse_min_rvol": scanner.get("dark_horse_min_rvol", 3.0),
                "dark_horse_min_gap_pct": scanner.get("dark_horse_min_gap_pct", 1.5),
                "dark_horse_min_volume_rank": scanner.get("dark_horse_min_volume_rank", 0.9),
                "session_allocation": scanner.get("session_allocation", {}),
            })
            # Also load blacklist
            defaults["blacklist"] = data.get("blacklist", {}).get("tickers", [])
    except Exception as e:
        log.warning("Failed to load config.toml: %s", e)

    _config_cache = defaults
    return defaults


# ---------------------------------------------------------------------------
# Contract loader
# ---------------------------------------------------------------------------
def _load_all_contracts() -> List[Dict[str, Any]]:
    """Load all contracts from contracts.toml.

    Returns list of dicts with: symbol, exchange, currency, leverage, sector.
    """
    try:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return []

        path = CONTRACTS_FILE
        if not path.exists():
            path = Path(__file__).resolve().parents[2] / "config" / "contracts.toml"
        if not path.exists():
            log.warning("contracts.toml not found at %s", path)
            return []

        with open(path, "rb") as f:
            data = tomllib.load(f)

        contracts = []
        for c in data.get("contracts", []):
            if c.get("symbol"):
                contracts.append({
                    "symbol": c["symbol"],
                    "exchange": c.get("exchange", "SMART"),
                    "currency": c.get("currency", "USD"),
                    "leverage": c.get("leverage", 1),
                    "sector": c.get("sector", "Unknown"),
                    "sec_type": c.get("sec_type", "STK"),
                })
        log.info("Loaded %d contracts from %s", len(contracts), path)
        return contracts
    except Exception as e:
        log.warning("Failed to load contracts: %s", e)
        return []


# ---------------------------------------------------------------------------
# NDJSON interaction logger
# ---------------------------------------------------------------------------
def _log_interaction(
    action: str,
    prompt_chars: int,
    response: Optional[Any],
    latency_ms: float,
    error: str = "",
    result_count: int = 0,
) -> None:
    """Append a record to gemini_scanner.ndjson for audit trail."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "prompt_chars": prompt_chars,
        "latency_ms": round(latency_ms, 1),
        "error": error,
        "result_count": result_count,
        "response_type": type(response).__name__ if response else "None",
    }
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        log.warning("Failed to write scanner log: %s", e)


# ---------------------------------------------------------------------------
# JSON parsing from LLM text
# ---------------------------------------------------------------------------
def _parse_json_from_text(text: str) -> Optional[Any]:
    """Parse JSON from Gemini's response, handling markdown code fences."""
    if not text:
        return None

    cleaned = text.strip()

    # Strip markdown code fences if present
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

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find JSON array within text
    bracket_start = cleaned.find("[")
    bracket_end = cleaned.rfind("]")
    if bracket_start >= 0 and bracket_end > bracket_start:
        try:
            return json.loads(cleaned[bracket_start:bracket_end + 1])
        except json.JSONDecodeError:
            pass

    # Try to find JSON object within text
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(cleaned[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _extract_ticker_list(parsed: Any, all_symbols: set) -> List[str]:
    """Extract a list of ticker symbols from parsed JSON.

    Handles various response formats:
    - Direct list of strings: ["QQQ3.L", "NVD3.L", ...]
    - List of objects: [{"ticker": "QQQ3.L", ...}, ...]
    - Dict with tickers key: {"tickers": ["QQQ3.L", ...]}
    - Dict with core/dark_horse key

    Filters to only include symbols that exist in contracts.toml.
    """
    if parsed is None:
        return []

    raw_list = []

    if isinstance(parsed, list):
        raw_list = parsed
    elif isinstance(parsed, dict):
        # Try common key names
        for key in ("tickers", "core", "dark_horses", "dark_horse", "symbols",
                     "core_tickers", "recommended", "focus_tickers"):
            if key in parsed and isinstance(parsed[key], list):
                raw_list = parsed[key]
                break

    # Normalize: extract symbol strings from items
    symbols = []
    for item in raw_list:
        if isinstance(item, str):
            sym = item.strip()
            if sym:
                symbols.append(sym)
        elif isinstance(item, dict):
            # Try common field names
            for field in ("ticker", "symbol", "name", "code"):
                val = item.get(field)
                if isinstance(val, str) and val.strip():
                    symbols.append(val.strip())
                    break

    # Filter to valid contract symbols only
    if all_symbols:
        valid = [s for s in symbols if s in all_symbols]
        if len(valid) < len(symbols):
            log.debug("Filtered %d/%d tickers to valid contracts",
                      len(valid), len(symbols))
        return valid

    return symbols


# ---------------------------------------------------------------------------
# Core Gemini API call
# ---------------------------------------------------------------------------
def _call_gemini(prompt: str, timeout: int = TIMEOUT_CORE) -> Optional[str]:
    """Call Gemini API and return the raw response text.

    Uses google.generativeai SDK: genai.configure(api_key=...) then
    model.generate_content(prompt).

    Retries up to MAX_RETRIES times with exponential backoff.
    Returns None on all-retries exhausted.
    """
    if not _ensure_genai():
        log.warning("Gemini SDK not available, cannot make API call")
        return None

    model = _genai.GenerativeModel(GEMINI_MODEL)
    last_error = ""

    for attempt in range(MAX_RETRIES):
        try:
            log.info(
                "Gemini API call (attempt %d/%d, timeout=%ds, prompt=%d chars)",
                attempt + 1, MAX_RETRIES, timeout, len(prompt),
            )

            # The google-generativeai SDK uses request_options for timeout
            # Disable safety filters — financial content triggers false positives
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            response = model.generate_content(
                prompt,
                generation_config=_genai.types.GenerationConfig(
                    temperature=0.2,  # Low temperature for deterministic output
                    max_output_tokens=16384,
                ),
                safety_settings=safety_settings,
                request_options={"timeout": timeout},
            )

            if response and response.text:
                return response.text.strip()

            # Check for blocked/empty response
            if response and hasattr(response, "prompt_feedback"):
                feedback = response.prompt_feedback
                if hasattr(feedback, "block_reason") and feedback.block_reason:
                    last_error = f"blocked: {feedback.block_reason}"
                    log.warning("Gemini response blocked: %s", last_error)
                    return None  # Don't retry blocked requests

            last_error = "empty response"
            log.warning("Gemini returned empty response (attempt %d/%d)",
                        attempt + 1, MAX_RETRIES)

        except Exception as e:
            last_error = str(e)
            err_lower = last_error.lower()

            # Check for rate limiting
            if "429" in last_error or "quota" in err_lower or "rate" in err_lower:
                backoff = BACKOFF_BASE ** (attempt + 1)
                log.warning("Gemini rate limited (attempt %d/%d), backing off %.0fs: %s",
                            attempt + 1, MAX_RETRIES, backoff, last_error)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                continue

            # Check for timeout
            if "timeout" in err_lower or "deadline" in err_lower:
                log.error("Gemini timeout (%ds, attempt %d/%d): %s",
                          timeout, attempt + 1, MAX_RETRIES, last_error)
            else:
                log.error("Gemini API error (attempt %d/%d): %s",
                          attempt + 1, MAX_RETRIES, last_error)

        if attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_BASE ** attempt)

    log.error("Gemini API: all %d retries exhausted. Last error: %s",
              MAX_RETRIES, last_error)
    return None


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------
# Exchange to canonical session mapping
_EXCHANGE_TO_SESSION = {
    "TSE": "ASIAN", "HKEX": "ASIAN", "SGX": "ASIAN",
    "LSEETF": "EUROPEAN", "LSE": "EUROPEAN", "XETRA": "EUROPEAN",
    "EURONEXT": "EUROPEAN", "AEB": "EUROPEAN",
    "SMART": "US", "NYSE": "US", "NASDAQ": "US", "AMEX": "US",
}

# Session time windows (UTC hours, inclusive start, exclusive end)
_SESSION_WINDOWS = {
    "ASIAN":    (23, 8),   # 23:00-08:00 UTC (wraps midnight)
    "EUROPEAN": (7, 17),   # 07:00-16:30 UTC (approx)
    "US":       (13, 21),  # 13:30-21:00 UTC (approx)
    "OVERLAP":  (13, 17),  # US + LSE overlap
}


def _detect_current_session() -> str:
    """Detect current trading session based on UTC hour."""
    hour = datetime.now(timezone.utc).hour

    # Overlap: US + European both open
    if 13 <= hour < 17:
        return "OVERLAP"
    # US session
    if 13 <= hour < 21:
        return "US"
    # European session
    if 7 <= hour < 17:
        return "EUROPEAN"
    # Asian session (wraps midnight)
    if hour >= 23 or hour < 8:
        return "ASIAN"

    # Dark window (21:00-23:00 UTC)
    return "EUROPEAN"  # Default to European for safety


def _get_session_exchanges(session: str) -> List[str]:
    """Get list of exchange codes active during a session."""
    mapping = {
        "ASIAN": ["TSE", "HKEX", "SGX"],
        "EUROPEAN": ["LSEETF", "XETRA", "EURONEXT"],
        "US": ["SMART"],
        "OVERLAP": ["LSEETF", "XETRA", "EURONEXT", "SMART"],
    }
    return mapping.get(session, ["LSEETF", "SMART"])


# ---------------------------------------------------------------------------
# Memory-based recurring winners (always in core, never rotated out)
# ---------------------------------------------------------------------------
def _get_recurring_winners(max_winners: int = 40) -> List[str]:
    """Load tickers with proven recurring TypeB edge from system_memory.json.

    These tickers have generated profitable trades consistently over multiple
    sessions. They get PRIORITY in core slots but are NOT locked — if their
    WR decays below threshold on the next nightly run, they drop out naturally.

    Logic:
    1. Read system_memory.json → per-ticker trade history
    2. Filter: >= 5 trades AND WR > 50% AND PF > 1.5
    3. Rank by (WR × PF × sqrt(trades))
    4. Return top max_winners

    Falls back to hardcoded institutional hot-path if no memory exists.
    """
    winners = []

    # Try reading from system_memory.json (Ouroboros learning output)
    try:
        memory_path = LOG_DIR / "system_memory.json"
        if not memory_path.exists():
            memory_path = Path("/app/data/system_memory.json")
        if memory_path.exists():
            with open(memory_path) as f:
                memory = json.load(f)

            # Per-ticker performance from backfill feedback or WAL analysis
            ticker_perf = memory.get("per_ticker_performance", {})
            if not ticker_perf:
                # Fallback: read from backfill_feedback.json
                feedback_path = LOG_DIR / "backfill_feedback.json"
                if not feedback_path.exists():
                    feedback_path = Path("/app/data/backfill_feedback.json")
                if feedback_path.exists():
                    with open(feedback_path) as f:
                        feedback = json.load(f)
                    ticker_perf = feedback.get("per_ticker", {})

            for ticker, stats in ticker_perf.items():
                trades = stats.get("trades", 0)
                wins = stats.get("wins", 0)
                if trades < 5:
                    continue
                wr = wins / trades
                total_pnl = stats.get("total_pnl", 0)
                # Approximate PF from WR (PF = WR * avg_win / ((1-WR) * avg_loss))
                # If total_pnl > 0 and WR > 0.50, it's a winner
                if wr > 0.50 and total_pnl > 0:
                    import math
                    score = wr * math.sqrt(trades) * (1 + total_pnl / max(abs(total_pnl), 1))
                    winners.append((ticker, score, wr, trades))

            winners.sort(key=lambda x: -x[1])
            if winners:
                log.info("Recurring winners from memory: %d qualified (top: %s WR=%.0f%% %d trades)",
                         len(winners), winners[0][0], winners[0][2]*100, winners[0][3])
    except Exception as e:
        log.warning("Failed to load recurring winners: %s", e)

    if not winners:
        # Fallback: institutional hot-path (hardcoded proven performers)
        # These are the tickers that ALWAYS produce TypeB signals based on backtests
        _INSTITUTIONAL_CORE = [
            # US high-beta (highest TypeB WR globally)
            "NVDA", "TSLA", "MSTR", "SMCI", "AMD", "COIN", "MARA",
            "TQQQ", "SQQQ", "SOXL", "SOXS", "SPY", "QQQ",
            # TSE semiconductor equipment (73% TypeF WR, 56% TypeB WR)
            "8035", "6857", "6146", "6920", "9984", "6758",
            # LSE leveraged ETPs (volume anomaly amplifiers)
            "QQQ3.L", "NVD3.L", "3LTS.L", "3LNV.L", "3LMS.L",
            # HKEX China tech (81.8% TypeB WR on HKEX)
            "0700", "9988", "1810", "1211", "3690",
        ]
        return _INSTITUTIONAL_CORE[:max_winners]

    return [w[0] for w in winners[:max_winners]]


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def _build_core_universe_prompt(
    current_session: str,
    contracts: List[Dict[str, Any]],
    market_data: Dict[str, Any],
    core_slots: int,
) -> str:
    """Build the prompt for core universe scanning."""
    # Group contracts by exchange
    by_exchange: Dict[str, List[str]] = {}
    for c in contracts:
        exch = c.get("exchange", "SMART")
        by_exchange.setdefault(exch, []).append(c["symbol"])

    # Build contract summary
    exchange_summary = []
    for exch, syms in sorted(by_exchange.items()):
        exchange_summary.append(f"  {exch}: {len(syms)} contracts")

    # Include market data summary if available
    market_summary = ""
    if market_data:
        top_volume = sorted(
            ((k, v.get("volume", 0)) for k, v in market_data.items() if isinstance(v, dict)),
            key=lambda x: x[1],
            reverse=True,
        )[:30]
        if top_volume:
            vol_lines = [f"  {sym}: vol={vol:,.0f}" for sym, vol in top_volume if vol > 0]
            if vol_lines:
                market_summary = "\n--- TOP 30 BY VOLUME ---\n" + "\n".join(vol_lines[:30])

    # Active exchanges for this session
    active_exchanges = _get_session_exchanges(current_session)

    # All available symbols for the prompt
    all_symbols = [c["symbol"] for c in contracts]
    # Limit to symbols from active exchanges for the prompt body
    session_symbols = [
        c["symbol"] for c in contracts
        if c.get("exchange", "SMART") in active_exchanges
    ]

    prompt = f"""You are AEGIS V2's universe curator. Select the top {core_slots} tickers for real-time streaming.

CURRENT SESSION: {current_session}
ACTIVE EXCHANGES: {', '.join(active_exchanges)}
UTC TIME: {datetime.now(timezone.utc).strftime('%H:%M')}

AVAILABLE UNIVERSE ({len(contracts)} contracts):
{chr(10).join(exchange_summary)}

SESSION TICKERS ({len(session_symbols)} from active exchanges):
{', '.join(session_symbols[:200])}{'...' if len(session_symbols) > 200 else ''}

{market_summary}

AEGIS SELECTION RULES (derived from 33 Rust risk CHECKs + Python bridge gates):

MUST-HAVE (hard requirements — ticker WILL NOT TRADE without these):
- Average Daily Volume > 1M shares (our RVOL 2.5x threshold needs a meaningful baseline)
- Typical bid-ask spread < 0.3% for stocks, < 2.0% for 3x leveraged ETPs (CHECK 13: spread veto)
- Market cap > $500M (institutional interest creates detectable order flow)
- ATR > 1.0% daily (Chandelier exit at 1.5x ATR needs price movement to capture profit)
- NOT in blacklist: 3LGO.L, 3LSI.L, LQQ3.L, 3LRR.L (proven losers from Wilson score)
- Exchange must be OPEN or opening within 2 hours (blackout gate rejects after entry cutoff)

STRONG PREFERENCE (increases probability of TypeB signal firing):
- Beta > 1.2 vs broad market (amplifies directional moves on volume surges)
- High options open interest (gamma exposure creates self-reinforcing volume spikes)
- Recent news sensitivity (earnings within 2 weeks, regulatory catalysts, sector rotation)
- Semiconductor/AI/crypto/defense/energy sectors (highest RVOL spike frequency in backtest)
- Leveraged 3x/5x ETPs during their underlying's session (volume anomalies amplified)
- Hurst exponent likely > 0.50 (trending stocks pass the Hurst regime gate at H > 0.45)

AVOID (will be rejected by gates even if selected):
- Stocks with typical spread > 0.5% (CHECK 29: minimum gross edge filter)
- Dead/range-bound stocks with ATR < 0.5% (Chandelier stop triggers immediately)
- Stocks in confirmed bearish regime with no catalyst (Hurst gate blocks mean-reverting momentum)
- ADV < 500K (RVOL spikes are noise, not institutional signal)
- Stocks halted or pending reverse split (CHECK 23: ticker halt gate)

SESSION-AWARE ALLOCATION:
- During ASIAN (23:00-08:00 UTC): Prioritize TSE (25), HKEX (20), SGX (5), with 10 pre-market LSE/XETRA
- During EUROPEAN (07:00-16:30 UTC): Prioritize LSE (25), XETRA (10), EURONEXT (5), with 10 pre-market US
- During US (13:30-21:00 UTC): Prioritize US (40), with 10 overnight TSE/HKEX for next session
- During OVERLAP (13:30-16:30 UTC): Split US (25) + LSE (20), with 10 from other exchanges
- Always reserve 10 slots for off-session pre-market scanning of upcoming exchanges

BACKTEST-PROVEN STRONGEST TICKERS (from 10.8M trade simulation):
- TSE semiconductor equipment: 8035.T, 6857.T, 6146.T, 6920.T (73% TypeF WR, 56% TypeB WR)
- US high-beta tech: NVDA, TSLA, SMCI, MSTR, AMD (72.7% TypeB WR on US exchange)
- LSE 3x ETPs: QQQ3.L, NVD3.L, 3LTS.L, 3LNV.L (volume anomalies from underlying moves)
- US 3x leveraged ETFs: TQQQ, SQQQ, SOXL, SOXS (highest intraday RVOL globally)
- HKEX China tech: 0700.HK, 9988.HK, 1810.HK (81.8% TypeB WR on HKEX)
- Crypto proxies: COIN, MARA, MSTR (extreme RVOL on BTC moves)

OUTPUT FORMAT (pure JSON array of ticker symbols, no markdown):
["{session_symbols[0] if session_symbols else 'QQQ3.L'}", "{session_symbols[1] if len(session_symbols) > 1 else 'NVD3.L'}", ...]

Return exactly {core_slots} ticker symbols. Return ONLY the JSON array."""

    return prompt


def _build_dark_horse_prompt(
    contracts: List[Dict[str, Any]],
    market_data: Dict[str, Any],
    dark_horse_slots: int,
    config: Dict[str, Any],
) -> str:
    """Build the prompt for dark horse ticker scanning."""
    min_rvol = config.get("dark_horse_min_rvol", 3.0)
    min_gap = config.get("dark_horse_min_gap_pct", 1.5)

    # Build market data summary for unusual activity
    unusual_movers = []
    if market_data:
        for sym, data in market_data.items():
            if not isinstance(data, dict):
                continue
            rvol = data.get("rvol", 0)
            gap_pct = abs(data.get("gap_pct", 0))
            change_pct = abs(data.get("change_pct", 0))
            volume = data.get("volume", 0)

            # Flag as potentially interesting
            if rvol >= min_rvol or gap_pct >= min_gap or change_pct >= 2.0:
                unusual_movers.append({
                    "symbol": sym,
                    "rvol": round(rvol, 1),
                    "gap_pct": round(data.get("gap_pct", 0), 2),
                    "change_pct": round(data.get("change_pct", 0), 2),
                    "volume": volume,
                })

    unusual_movers.sort(key=lambda x: x.get("rvol", 0), reverse=True)

    movers_text = ""
    if unusual_movers:
        movers_lines = []
        for m in unusual_movers[:50]:
            movers_lines.append(
                f"  {m['symbol']}: RVOL={m['rvol']}x gap={m['gap_pct']}% chg={m['change_pct']}% vol={m['volume']:,.0f}"
            )
        movers_text = "\n--- UNUSUAL ACTIVITY DETECTED ---\n" + "\n".join(movers_lines)

    all_symbols = [c["symbol"] for c in contracts]

    prompt = f"""You are AEGIS V2's dark horse detector. Find {dark_horse_slots} tickers showing unusual activity NOW.

UTC TIME: {datetime.now(timezone.utc).strftime('%H:%M')}

DARK HORSE CRITERIA (ticker qualifies if ANY is met):
- RVOL > {min_rvol}x (volume spike vs 20-day average)
- Gap > {min_gap}% from previous close
- Price change > 2% intraday
- Unusual volume rank (top 10% for the day)

{movers_text if movers_text else "--- NO MARKET DATA AVAILABLE (use best judgment from universe) ---"}

AVAILABLE UNIVERSE: {len(contracts)} tickers
{', '.join(all_symbols[:100])}{'...' if len(all_symbols) > 100 else ''}

DARK HORSE RULES (from AEGIS 33-CHECK risk arbiter + bridge gates):
- MUST have ADV > 500K (below this, RVOL spikes are noise not institutional flow)
- MUST have typical spread < 0.5% (CHECK 29 min gross edge rejects wide-spread tickers)
- MUST be on an exchange that is CURRENTLY OPEN (blackout gate rejects closed exchanges)
- Prioritize highest RVOL first (TypeB core signal = RVOL > 2.5x triggers entry)
- Gap > 1.5% suggests overnight catalyst — monitor for continuation volume
- Prefer high-beta sectors: semiconductors, AI, crypto, defense, energy, EV
- Prefer leveraged ETPs (3x/5x) when underlying is in play (amplified volume signature)
- Do NOT select range-bound stocks (ATR < 0.5%) — Chandelier exit needs movement
- Do NOT select blacklisted: 3LGO.L, 3LSI.L, LQQ3.L, 3LRR.L (proven losers)
- Do NOT duplicate tickers already in the core 80 — dark horses are ADDITIVE coverage
- Ideally select stocks with Hurst > 0.50 (trending regime passes the bridge Hurst gate at 0.45)
- Session context: if ASIAN hours, prioritize TSE/HKEX dark horses. If US hours, prioritize US.

OUTPUT FORMAT (pure JSON array of ticker symbols, no markdown):
["TICKER1", "TICKER2", ...]

Return exactly {dark_horse_slots} ticker symbols. Return ONLY the JSON array."""

    return prompt


def _build_morning_brief_prompt(
    date: str,
    market_regime: str,
    recent_trades: List[Dict[str, Any]],
    contracts: List[Dict[str, Any]],
) -> str:
    """Build the prompt for morning pre-market brief."""
    # Summarize recent trades
    trade_summary = "No recent trades available."
    if recent_trades:
        wins = sum(1 for t in recent_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in recent_trades if t.get("pnl", 0) < 0)
        total_pnl = sum(t.get("pnl", 0) for t in recent_trades)
        win_rate = wins / max(len(recent_trades), 1)

        # Top 5 recent trades by recency
        recent_5 = recent_trades[-5:]
        trade_lines = []
        for t in recent_5:
            trade_lines.append(
                f"  {t.get('symbol', '?')} {t.get('direction', '?')}: "
                f"PnL={t.get('pnl', 0):.2f} conf={t.get('confidence', 0)} "
                f"strategy={t.get('strategy', '?')}"
            )

        trade_summary = (
            f"Recent: {len(recent_trades)} trades, WR={win_rate:.0%}, "
            f"PnL={total_pnl:.2f}, W={wins} L={losses}\n"
            f"Last 5:\n" + "\n".join(trade_lines)
        )

    # Exchange count summary
    by_exchange: Dict[str, int] = {}
    for c in contracts:
        exch = c.get("exchange", "SMART")
        by_exchange[exch] = by_exchange.get(exch, 0) + 1

    prompt = f"""You are AEGIS V2's morning intelligence analyst. Prepare the pre-market brief for {date}.

CURRENT REGIME: {market_regime}
UNIVERSE: {len(contracts)} contracts across {len(by_exchange)} exchanges

EXCHANGE BREAKDOWN:
{chr(10).join(f'  {e}: {n} contracts' for e, n in sorted(by_exchange.items()))}

RECENT PERFORMANCE:
{trade_summary}

ANALYSIS REQUIRED:
1. Which tickers should we FOCUS on today (top 10-15)?
2. Which tickers should we AVOID today (risk of gap-down, earnings miss, etc.)?
3. What is your regime assessment for today?
4. How should strategy weights be adjusted?

OUTPUT FORMAT (pure JSON, no markdown):
{{
  "date": "{date}",
  "focus_tickers": ["TICKER1", "TICKER2", ...],
  "avoid_tickers": ["TICKER1", "TICKER2", ...],
  "regime_assessment": "<1-2 sentences on current regime and expected behavior>",
  "strategy_weights": {{
    "F_MOM": 0.0-1.0,
    "F_REV": 0.0-1.0,
    "F_MAC": 0.0-1.0,
    "F_DIS": 0.0-1.0
  }},
  "key_events": ["<any known economic events today>"],
  "risk_notes": ["<risk items to watch>"],
  "confidence": 0.0-1.0
}}

REGIME-BASED STRATEGY RULES:
- Trending (Hurst > 0.55): F_MOM high, F_REV low
- Mean-reverting (Hurst < 0.45): F_REV high, F_MOM low
- Random walk: balanced, reduce sizing
- After losses: conservative, reduce F_DIS

Be concise. Return ONLY the JSON object."""

    return prompt


# ---------------------------------------------------------------------------
# Deterministic fallbacks
# ---------------------------------------------------------------------------
def _fallback_core_universe(
    current_session: str,
    contracts: List[Dict[str, Any]],
    market_data: Dict[str, Any],
    core_slots: int,
) -> List[str]:
    """Deterministic fallback: sort by volume x leverage x session_weight.

    Used when Gemini API is unavailable.
    """
    config = _load_config()
    blacklist = set(config.get("blacklist", []))
    active_exchanges = set(_get_session_exchanges(current_session))

    scored = []
    for c in contracts:
        sym = c["symbol"]
        if sym in blacklist:
            continue

        exchange = c.get("exchange", "SMART")
        leverage = c.get("leverage", 1)

        # Session weight: in-session exchanges get 2x boost
        session_weight = 2.0 if exchange in active_exchanges else 0.5

        # Volume from market data (or 0 if unavailable)
        vol = 0.0
        if market_data and sym in market_data and isinstance(market_data[sym], dict):
            vol = float(market_data[sym].get("volume", 0))
            atr = float(market_data[sym].get("atr", 1.0))
        else:
            # Default: leverage as proxy for desirability
            vol = 100000.0 * leverage
            atr = 1.0

        score = vol * atr * session_weight * (1 + leverage * 0.1)
        scored.append((sym, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [sym for sym, _ in scored[:core_slots]]

    log.info("Fallback core universe: %d tickers (session=%s)", len(result), current_session)
    return result


def _fallback_dark_horses(
    contracts: List[Dict[str, Any]],
    market_data: Dict[str, Any],
    dark_horse_slots: int,
) -> List[str]:
    """Deterministic fallback: sort by RVOL descending, take top N.

    Used when Gemini API is unavailable.
    """
    config = _load_config()
    blacklist = set(config.get("blacklist", []))

    scored = []
    for c in contracts:
        sym = c["symbol"]
        if sym in blacklist:
            continue
        if market_data and sym in market_data and isinstance(market_data[sym], dict):
            rvol = float(market_data[sym].get("rvol", 0))
            gap_pct = abs(float(market_data[sym].get("gap_pct", 0)))
            change_pct = abs(float(market_data[sym].get("change_pct", 0)))
            # Combined unusual activity score
            score = rvol * 2.0 + gap_pct + change_pct
            if score > 0:
                scored.append((sym, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [sym for sym, _ in scored[:dark_horse_slots]]

    log.info("Fallback dark horses: %d tickers", len(result))
    return result


def _fallback_morning_brief(
    date: str,
    market_regime: str,
    recent_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Deterministic fallback morning brief."""
    # Strategy weights based on regime
    if "trending" in market_regime.lower() or "trend" in market_regime.lower():
        weights = {"F_MOM": 0.45, "F_REV": 0.15, "F_MAC": 0.25, "F_DIS": 0.15}
    elif "mean" in market_regime.lower() or "revert" in market_regime.lower():
        weights = {"F_MOM": 0.15, "F_REV": 0.45, "F_MAC": 0.25, "F_DIS": 0.15}
    else:
        weights = {"F_MOM": 0.30, "F_REV": 0.25, "F_MAC": 0.25, "F_DIS": 0.20}

    # Reduce F_DIS after consecutive losses
    if recent_trades:
        consecutive_losses = 0
        for t in reversed(recent_trades):
            if t.get("pnl", 0) < 0:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= 3:
            weights["F_DIS"] = max(0.05, weights["F_DIS"] - 0.10)
            weights["F_MOM"] = min(0.50, weights["F_MOM"] + 0.05)
            weights["F_REV"] = min(0.50, weights["F_REV"] + 0.05)

    return {
        "date": date,
        "focus_tickers": [],
        "avoid_tickers": [],
        "regime_assessment": f"[FALLBACK] Regime={market_regime}. Gemini unavailable — using deterministic weights.",
        "strategy_weights": weights,
        "key_events": [],
        "risk_notes": ["Gemini API unavailable — using deterministic fallback"],
        "confidence": 0.0,
        "fallback": True,
    }


# ---------------------------------------------------------------------------
# Simulation mode — deterministic mock responses
# ---------------------------------------------------------------------------
def _sim_core_universe(
    current_session: str,
    contracts: List[Dict[str, Any]],
    core_slots: int,
) -> List[str]:
    """Return mock core universe for simulation/testing."""
    active_exchanges = set(_get_session_exchanges(current_session))

    # Prefer session-appropriate tickers, then fill with others
    session_tickers = [
        c["symbol"] for c in contracts
        if c.get("exchange", "SMART") in active_exchanges
    ]
    other_tickers = [
        c["symbol"] for c in contracts
        if c.get("exchange", "SMART") not in active_exchanges
    ]

    result = session_tickers[:core_slots]
    if len(result) < core_slots:
        result.extend(other_tickers[:core_slots - len(result)])

    log.info("[SIMULATION] Core universe: %d tickers (session=%s)", len(result), current_session)
    return result[:core_slots]


def _sim_dark_horses(contracts: List[Dict[str, Any]], dark_horse_slots: int) -> List[str]:
    """Return mock dark horse tickers for simulation/testing."""
    # Take leveraged tickers as "interesting" mock dark horses
    leveraged = [c["symbol"] for c in contracts if c.get("leverage", 1) >= 3]
    result = leveraged[:dark_horse_slots]
    if len(result) < dark_horse_slots:
        others = [c["symbol"] for c in contracts if c.get("leverage", 1) < 3]
        result.extend(others[:dark_horse_slots - len(result)])
    log.info("[SIMULATION] Dark horses: %d tickers", len(result))
    return result[:dark_horse_slots]


def _sim_morning_brief(date: str, market_regime: str) -> Dict[str, Any]:
    """Return mock morning brief for simulation/testing."""
    return {
        "date": date,
        "focus_tickers": ["QQQ3.L", "NVD3.L", "3LUS.L", "3SEM.L", "TSL3.L"],
        "avoid_tickers": ["3LGO.L", "3LSI.L"],
        "regime_assessment": f"[SIMULATION] Regime={market_regime}. Mock analysis — no real Gemini call.",
        "strategy_weights": {"F_MOM": 0.35, "F_REV": 0.25, "F_MAC": 0.25, "F_DIS": 0.15},
        "key_events": ["[SIMULATION] No real event data"],
        "risk_notes": ["Simulation mode active — no real Gemini analysis"],
        "confidence": 0.0,
        "simulation": True,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def scan_core_universe(
    current_session: Optional[str] = None,
    all_contracts: Optional[List[Dict[str, Any]]] = None,
    market_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Scan the global market and recommend the top 80 core tickers for streaming.

    Calls Gemini with session context, exchange allocations, and volume data.
    Falls back to deterministic volume x ATR x session_weight scoring.

    Args:
        current_session: One of ASIAN, EUROPEAN, US, OVERLAP. Auto-detected if None.
        all_contracts: List of contract dicts from contracts.toml. Loaded if None.
        market_data: Dict of {symbol: {volume, atr, rvol, ...}}. Optional.

    Returns:
        List of ticker symbols (max 80) for core streaming slots.
    """
    if current_session is None:
        current_session = _detect_current_session()
    if all_contracts is None:
        all_contracts = _load_all_contracts()
    if market_data is None:
        market_data = {}

    config = _load_config()
    core_slots = config.get("core_slots", CORE_SLOTS)
    all_symbols = {c["symbol"] for c in all_contracts}

    # 3-SOURCE CORE ALLOCATION:
    # 1. Memory winners: highest priority but NOT locked — dropped if WR decays
    # 2. IBKR scanner: live data, top performers from exchange-wide scans
    # 3. Gemini: strategic intelligence, catalyst/sector awareness
    # All 160 are competitive — best tickers across all 3 sources win slots.
    recurring = _get_recurring_winners(max_winners=min(40, core_slots // 4))
    reserved_count = len(recurring)

    # Read IBKR scanner results for additional high-RVOL tickers
    ibkr_picks: List[str] = []
    try:
        scanner_path = LOG_DIR / "scanner_results.json"
        if not scanner_path.exists():
            scanner_path = Path("/app/data/scanner_results.json")
        if scanner_path.exists():
            import time as _time_mod
            scanner_age = _time_mod.time() - scanner_path.stat().st_mtime
            if scanner_age < 1800:  # Only use if < 30 min old
                with open(scanner_path) as f:
                    scanner_data = json.load(f)
                # Collect all unique tickers across all scanner types
                seen_ibkr = set(recurring)
                for scan_name, scan_info in scanner_data.get("scanners", {}).items():
                    for result in scan_info.get("results", []):
                        sym = result.get("symbol", "")
                        if sym and sym not in seen_ibkr:
                            ibkr_picks.append(sym)
                            seen_ibkr.add(sym)
                            if len(ibkr_picks) >= 60:
                                break
                    if len(ibkr_picks) >= 60:
                        break
                log.info("IBKR scanner contributed %d tickers to core", len(ibkr_picks))
    except Exception as e:
        log.warning("Failed to read IBKR scanner results: %s", e)

    gemini_slots = core_slots - reserved_count - len(ibkr_picks)
    log.info("Core allocation: %d memory + %d IBKR scanner + %d Gemini = %d total",
             reserved_count, len(ibkr_picks), gemini_slots, core_slots)

    # Simulation mode
    if SIMULATION_MODE:
        result = _sim_core_universe(current_session, all_contracts, core_slots)
        _log_interaction("scan_core_universe", 0, result, 0.0, result_count=len(result))
        return result

    # Build prompt and call Gemini
    prompt = _build_core_universe_prompt(current_session, all_contracts, market_data, core_slots)
    t0 = time.monotonic()
    raw = _call_gemini(prompt, timeout=TIMEOUT_CORE)
    latency_ms = (time.monotonic() - t0) * 1000

    if raw is None:
        # Gemini unavailable — use deterministic fallback
        result = _fallback_core_universe(current_session, all_contracts, market_data, core_slots)
        _log_interaction("scan_core_universe", len(prompt), result, latency_ms,
                         error="gemini_unavailable", result_count=len(result))
        return result

    # Parse response
    parsed = _parse_json_from_text(raw)
    tickers = _extract_ticker_list(parsed, all_symbols)

    if len(tickers) < 10:
        # Parse failure or too few results — fall back
        log.warning("Gemini returned only %d valid tickers, falling back to deterministic",
                    len(tickers))
        result = _fallback_core_universe(current_session, all_contracts, market_data, core_slots)
        _log_interaction("scan_core_universe", len(prompt), result, latency_ms,
                         error=f"insufficient_results ({len(tickers)})", result_count=len(result))
        return result

    # Merge: recurring winners FIRST, then IBKR scanner, then Gemini picks (deduplicated)
    merged = list(recurring)  # Recurring winners always first (memory-locked)
    existing = set(recurring)
    for sym in ibkr_picks:  # IBKR scanner picks second (live data)
        if sym not in existing:
            merged.append(sym)
            existing.add(sym)
    for sym in tickers:
        if sym not in existing:
            merged.append(sym)
            existing.add(sym)
            if len(merged) >= core_slots:
                break

    # Pad with fallback if still short
    if len(merged) < core_slots:
        fallback = _fallback_core_universe(current_session, all_contracts, market_data, core_slots)
        for sym in fallback:
            if sym not in existing:
                merged.append(sym)
                existing.add(sym)
                if len(merged) >= core_slots:
                    break

    tickers = merged[:core_slots]

    log.info("Core universe scan: %d tickers (%d recurring + %d Gemini, session=%s, latency=%.0fms)",
             len(tickers), reserved_count, len(tickers) - reserved_count, current_session, latency_ms)
    _log_interaction("scan_core_universe", len(prompt), tickers, latency_ms,
                     result_count=len(tickers))
    return tickers


def scan_dark_horses(
    all_contracts: Optional[List[Dict[str, Any]]] = None,
    market_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Scan for unusual movers that should get the 20 rotating slots.

    Calls Gemini asking which tickers show unusual activity: RVOL spikes,
    gap opens, sudden price moves.

    Falls back to sorting by RVOL descending.

    Args:
        all_contracts: List of contract dicts. Loaded if None.
        market_data: Dict of {symbol: {rvol, gap_pct, change_pct, volume, ...}}.

    Returns:
        List of ticker symbols (max 20) for dark horse streaming slots.
    """
    if all_contracts is None:
        all_contracts = _load_all_contracts()
    if market_data is None:
        market_data = {}

    config = _load_config()
    dark_horse_slots = config.get("dark_horse_slots", DARK_HORSE_SLOTS)
    all_symbols = {c["symbol"] for c in all_contracts}

    # Simulation mode
    if SIMULATION_MODE:
        result = _sim_dark_horses(all_contracts, dark_horse_slots)
        _log_interaction("scan_dark_horses", 0, result, 0.0, result_count=len(result))
        return result

    # Build prompt and call Gemini
    prompt = _build_dark_horse_prompt(all_contracts, market_data, dark_horse_slots, config)
    t0 = time.monotonic()
    raw = _call_gemini(prompt, timeout=TIMEOUT_DARK_HORSE)
    latency_ms = (time.monotonic() - t0) * 1000

    if raw is None:
        result = _fallback_dark_horses(all_contracts, market_data, dark_horse_slots)
        _log_interaction("scan_dark_horses", len(prompt), result, latency_ms,
                         error="gemini_unavailable", result_count=len(result))
        return result

    # Parse response
    parsed = _parse_json_from_text(raw)
    tickers = _extract_ticker_list(parsed, all_symbols)

    if len(tickers) < 3:
        log.warning("Gemini returned only %d dark horses, falling back", len(tickers))
        result = _fallback_dark_horses(all_contracts, market_data, dark_horse_slots)
        _log_interaction("scan_dark_horses", len(prompt), result, latency_ms,
                         error=f"insufficient_results ({len(tickers)})", result_count=len(result))
        return result

    # Trim or pad
    if len(tickers) > dark_horse_slots:
        tickers = tickers[:dark_horse_slots]
    elif len(tickers) < dark_horse_slots:
        existing = set(tickers)
        fallback = _fallback_dark_horses(all_contracts, market_data, dark_horse_slots)
        for sym in fallback:
            if sym not in existing:
                tickers.append(sym)
                existing.add(sym)
                if len(tickers) >= dark_horse_slots:
                    break

    log.info("Dark horse scan: %d tickers (latency=%.0fms)", len(tickers), latency_ms)
    _log_interaction("scan_dark_horses", len(prompt), tickers, latency_ms,
                     result_count=len(tickers))
    return tickers


def morning_brief(
    date: Optional[str] = None,
    market_regime: str = "unknown",
    recent_trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Generate pre-market morning analysis.

    Calls Gemini for today's focus tickers, avoids, regime assessment,
    and strategy weight recommendations.

    Args:
        date: Date string (YYYY-MM-DD). Defaults to today UTC.
        market_regime: Current regime (e.g., "trending", "mean_reverting", "random").
        recent_trades: List of recent trade dicts with pnl, symbol, etc.

    Returns:
        Dict with: focus_tickers, avoid_tickers, regime_assessment,
                   strategy_weights, key_events, risk_notes, confidence.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if recent_trades is None:
        recent_trades = []

    contracts = _load_all_contracts()

    # Simulation mode
    if SIMULATION_MODE:
        result = _sim_morning_brief(date, market_regime)
        _log_interaction("morning_brief", 0, result, 0.0)
        return result

    # Build prompt and call Gemini
    prompt = _build_morning_brief_prompt(date, market_regime, recent_trades, contracts)
    t0 = time.monotonic()
    raw = _call_gemini(prompt, timeout=TIMEOUT_BRIEF)
    latency_ms = (time.monotonic() - t0) * 1000

    if raw is None:
        result = _fallback_morning_brief(date, market_regime, recent_trades)
        result["latency_ms"] = latency_ms
        _log_interaction("morning_brief", len(prompt), result, latency_ms,
                         error="gemini_unavailable")
        return result

    # Parse response
    parsed = _parse_json_from_text(raw)
    if parsed is None or not isinstance(parsed, dict):
        log.warning("Failed to parse Gemini morning brief response")
        result = _fallback_morning_brief(date, market_regime, recent_trades)
        result["latency_ms"] = latency_ms
        _log_interaction("morning_brief", len(prompt), result, latency_ms,
                         error="json_parse_failure")
        return result

    # Normalize and fill defaults
    result = {
        "date": parsed.get("date", date),
        "focus_tickers": parsed.get("focus_tickers", []),
        "avoid_tickers": parsed.get("avoid_tickers", []),
        "regime_assessment": parsed.get("regime_assessment", f"Regime: {market_regime}"),
        "strategy_weights": parsed.get("strategy_weights", {
            "F_MOM": 0.30, "F_REV": 0.25, "F_MAC": 0.25, "F_DIS": 0.20,
        }),
        "key_events": parsed.get("key_events", []),
        "risk_notes": parsed.get("risk_notes", []),
        "confidence": max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
        "latency_ms": latency_ms,
        "simulation": False,
        "fallback": False,
    }

    # Validate strategy weights sum to ~1.0
    weights = result["strategy_weights"]
    weight_sum = sum(weights.values())
    if weight_sum > 0 and abs(weight_sum - 1.0) > 0.1:
        # Normalize
        for k in weights:
            weights[k] = round(weights[k] / weight_sum, 3)

    log.info("Morning brief: %d focus, %d avoid, regime=%s, confidence=%.2f (latency=%.0fms)",
             len(result["focus_tickers"]), len(result["avoid_tickers"]),
             market_regime, result["confidence"], latency_ms)
    _log_interaction("morning_brief", len(prompt), result, latency_ms)
    return result


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def _write_scanner_output(action: str, data: Any) -> Path:
    """Write scanner output to a timestamped JSON file for the engine to consume."""
    output_dir = LOG_DIR / "gemini"
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = f"{action}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    output_path = output_dir / filename

    output = {
        "timestamp": now.isoformat(),
        "action": action,
        "data": data,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Also write a "latest" symlink/file for easy access
    latest_path = output_dir / f"{action}_latest.json"
    with open(latest_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    log.info("Scanner output written: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def _test_cli():
    """Test all scanner functions with mock or real Gemini calls."""
    log.info("=== Gemini Scanner Test Suite ===")
    log.info("SIMULATION_MODE=%s", SIMULATION_MODE)
    log.info("GEMINI_API_KEY set=%s", bool(os.environ.get(GEMINI_API_KEY_ENV)))

    contracts = _load_all_contracts()
    log.info("Loaded %d contracts", len(contracts))

    # Test core universe
    log.info("\n--- Testing scan_core_universe ---")
    session = _detect_current_session()
    core = scan_core_universe(session, contracts, {})
    log.info("Core universe (%s): %d tickers", session, len(core))
    if core:
        log.info("  First 10: %s", core[:10])
        log.info("  Last 5: %s", core[-5:])

    # Test dark horses
    log.info("\n--- Testing scan_dark_horses ---")
    # Create mock market data with some unusual movers
    mock_market = {}
    if contracts:
        for i, c in enumerate(contracts[:10]):
            mock_market[c["symbol"]] = {
                "rvol": 5.0 - (i * 0.3),
                "gap_pct": 2.5 - (i * 0.2),
                "change_pct": 3.0 - (i * 0.25),
                "volume": 500000 * (10 - i),
            }
    dark = scan_dark_horses(contracts, mock_market)
    log.info("Dark horses: %d tickers", len(dark))
    if dark:
        log.info("  All: %s", dark)

    # Test morning brief
    log.info("\n--- Testing morning_brief ---")
    brief = morning_brief(
        market_regime="trending",
        recent_trades=[
            {"symbol": "QQQ3.L", "pnl": 25.0, "confidence": 72, "strategy": "TypeB", "direction": "Long"},
            {"symbol": "NVD3.L", "pnl": -15.0, "confidence": 65, "strategy": "Orchestrator", "direction": "Long"},
        ],
    )
    log.info("Brief: %s", json.dumps(brief, indent=2))

    log.info("\n=== Test Suite Complete ===")


def main():
    """CLI entry point for scheduled scanner runs."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Gemini Scanner — LLM-powered universe curation for AEGIS V2",
    )
    parser.add_argument("--core", action="store_true",
                        help="Run core universe scan (80 tickers, 2-hour rotation)")
    parser.add_argument("--dark-horse", action="store_true",
                        help="Run dark horse scan (20 tickers, 15-min rotation)")
    parser.add_argument("--brief", action="store_true",
                        help="Run morning pre-market brief")
    parser.add_argument("--test", action="store_true",
                        help="Run test suite (all functions)")
    parser.add_argument("--sim", action="store_true",
                        help="Force simulation mode")
    parser.add_argument("--session", type=str, default=None,
                        choices=["ASIAN", "EUROPEAN", "US", "OVERLAP"],
                        help="Override session detection for --core")
    parser.add_argument("--regime", type=str, default="unknown",
                        help="Market regime for --brief (trending, mean_reverting, random)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [Gemini-Scanner] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.sim:
        global SIMULATION_MODE
        SIMULATION_MODE = True
        log.info("Forced SIMULATION_MODE=True")

    if args.test:
        _test_cli()
        return

    # Default: run all three if no specific action requested
    run_all = not (args.core or args.dark_horse or args.brief)

    if args.core or run_all:
        log.info("=" * 60)
        log.info("Gemini Core Universe Scan")
        log.info("=" * 60)
        session = args.session or _detect_current_session()
        core = scan_core_universe(current_session=session)
        _write_scanner_output("core_universe", {
            "session": session,
            "tickers": core,
            "count": len(core),
        })
        log.info("Core scan complete: %d tickers for session %s", len(core), session)

    if args.dark_horse or run_all:
        log.info("=" * 60)
        log.info("Gemini Dark Horse Scan")
        log.info("=" * 60)
        dark = scan_dark_horses()
        _write_scanner_output("dark_horses", {
            "tickers": dark,
            "count": len(dark),
        })
        log.info("Dark horse scan complete: %d tickers", len(dark))

    if args.brief or run_all:
        log.info("=" * 60)
        log.info("Gemini Morning Brief")
        log.info("=" * 60)
        brief = morning_brief(market_regime=args.regime)
        _write_scanner_output("morning_brief", brief)
        log.info("Morning brief complete: %d focus, %d avoid",
                 len(brief.get("focus_tickers", [])),
                 len(brief.get("avoid_tickers", [])))

    log.info("=" * 60)
    log.info("Gemini Scanner run complete")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
