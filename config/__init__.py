"""
NZT-48 Trading System — Configuration Loader
Loads settings.yaml and provides typed access to all configuration.
Environment variables override YAML for secrets (API keys, tokens).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml


_CONFIG: dict[str, Any] | None = None
_CONFIG_DIR = Path(__file__).parent  # config/ directory itself
_CONFIG_LOCK = threading.Lock()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the master YAML configuration file.

    Loads once and caches. Environment variables override YAML values
    for any key ending in _env (e.g. api_key_env -> reads from env).
    Thread-safe via _CONFIG_LOCK.
    """
    global _CONFIG
    with _CONFIG_LOCK:
        if _CONFIG is not None:
            return _CONFIG

        if path is None:
            path = _CONFIG_DIR / "settings.yaml"

        with open(path, "r") as f:
            loaded = yaml.safe_load(f)

        # Handle None from yaml.safe_load (empty file)
        if loaded is None:
            loaded = {}

        _CONFIG = loaded
        _resolve_env_vars(_CONFIG)
        return _CONFIG


def _resolve_env_vars(d: dict[str, Any], prefix: str = "") -> None:
    """Recursively resolve _env keys to their environment variable values."""
    if not isinstance(d, dict):
        return
    for key, value in list(d.items()):
        if isinstance(value, dict):
            _resolve_env_vars(value, f"{prefix}{key}.")
        elif isinstance(value, str) and key.endswith("_env"):
            resolved = os.environ.get(value, "")
            # Store the resolved value alongside the env key name
            base_key = key.replace("_env", "")
            d[base_key] = resolved


def get(key_path: str, default: Any = None) -> Any:
    """Get a nested config value using dot notation.

    Example: get("immutable_rules.risk_per_trade") -> 0.0075
    """
    cfg = load_config()
    keys = key_path.split(".")
    current = cfg
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    return current


def get_tickers() -> list[str]:
    """Return the Bot B universe of 18 US equities."""
    raw = get("bot_b_universe.tickers", [])
    # Guard against YAML parsing 'ON' / 'NO' / 'YES' as booleans
    return [str(t).upper() if not isinstance(t, str) else t for t in raw]


def get_isa_tickers() -> list[str]:
    """Return the full ISA scan universe — all leveraged ETPs eligible for S15/S16.

    Includes:
    - bot_a_universe (long_3x, inverse_3x, leveraged_4x_5x) — A/B-team
    - v2_engine.isa_tickers_v2.core_long/core_inverse — V3.2 pairs
    - v2_engine.isa_tickers_v2.extended — commodity + additional ETPs
    - v2_engine.isa_tickers_v2.core_expansion_v2 — B-team candidates (vol-gated)
    All tickers are eligible for scanning; PerformanceRelegation controls trade size.
    """
    tickers: list[str] = []

    # Primary bot_a_universe sections
    bot_a = get("bot_a_universe", {})
    for section_key in ("long_3x", "inverse_3x", "leveraged_4x_5x"):
        items = bot_a.get(section_key, [])
        for item in items:
            if isinstance(item, dict):
                t = item.get("ticker", "")
                # Skip VERIFY/low-liquidity tickers that haven't been confirmed
                if item.get("status") == "VERIFY" and item.get("min_vol_gate"):
                    avg_vol = item.get("avg_vol", 0)
                    if avg_vol < 5000:  # Below minimum volume threshold
                        continue
            else:
                t = str(item).upper()
            if t:
                tickers.append(t)

    # V3.2 core pairs from v2_engine config
    # FIX: was "strategy.isa_tickers_v2" — correct path is "v2_engine.isa_tickers_v2"
    v2 = get("v2_engine.isa_tickers_v2", {})
    for section_key in ("core_long", "core_inverse"):
        items = v2.get(section_key, [])
        for item in items:
            t = str(item).upper() if not isinstance(item, dict) else item.get("ticker", "")
            if t:
                tickers.append(t)

    # Extended universe — commodity ETPs (3OIL.L, 3SIL.L, 3GOL.L, etc.)
    extended = v2.get("extended", [])
    for item in extended:
        t = str(item).upper() if not isinstance(item, dict) else item.get("ticker", "")
        if t:
            tickers.append(t)

    # Core expansion v2 — B-team promotion candidates (min_vol_gate items with >5k avg vol)
    expansion = v2.get("core_expansion_v2", [])
    for item in expansion:
        if isinstance(item, dict):
            avg_vol = item.get("avg_vol", 0)
            if avg_vol >= 5000 and item.get("min_vol_gate", False):
                t = item.get("ticker", "")
                if t:
                    tickers.append(t)
        else:
            tickers.append(str(item).upper())

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in tickers:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)

    if not unique:
        # Fallback: hardcoded ISA universe (long tickers + inverse from F-03 SSOT)
        from config.universe_constants import INVERSE_ETPS_SET as _inv_set
        _long_tickers = [
            "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
            "TSL3.L", "TSM3.L", "MU2.L", "QQQ5.L", "SP5L.L",
        ]
        unique = _long_tickers + sorted(_inv_set)
    return unique


def get_primary_mode() -> str:
    """Return the primary trading mode: UK_ISA or US_EQUITY."""
    return get("v2_engine.primary_mode", "UK_ISA")


def get_ticker_override(ticker: str, param: str, default: Any = None) -> Any:
    """Get a per-ticker override value (Section 4)."""
    overrides = get(f"bot_b_universe.overrides.{ticker}", {})
    return overrides.get(param, default)


def get_immutable_rule(rule: str) -> Any:
    """Get an immutable risk rule (Section 43). These are CONSTITUTIONAL."""
    return get(f"immutable_rules.{rule}")


def get_bot_config(bot_name: str) -> dict[str, Any]:
    """Get configuration for a specific bot instance (Section 64)."""
    return get(f"bots.{bot_name}", {})


def is_paper_mode() -> bool:
    """Check if system is in paper trading mode."""
    return get("system.mode", "PAPER") == "PAPER"


def get_db_path() -> str:
    """Get the SQLite database path."""
    return get("system.db_path", "data/nzt48.db")


def reload() -> dict[str, Any]:
    """Force reload configuration from disk. Thread-safe."""
    global _CONFIG
    with _CONFIG_LOCK:
        _CONFIG = None
    return load_config()
