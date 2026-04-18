"""Portable path resolver — use this instead of hardcoding /Users/rr/aegis-v5/.

Usage:
    from python_brain.core.paths import ROOT, CONFIG, DATA, MODELS, ARCHIVE
    path = MODELS / "meta_labeler.pkl"

Respects V5_ROOT env var so the codebase runs from any location.
"""
from __future__ import annotations

import os
from pathlib import Path


def _resolve_root() -> Path:
    """Resolve V5 project root.

    Priority:
      1. V5_ROOT environment variable
      2. Walk up from this file's location until we find config/defaults.toml
      3. Fallback to /Users/rr/aegis-v5
    """
    env_root = os.environ.get("V5_ROOT")
    if env_root:
        p = Path(env_root)
        if p.exists():
            return p

    # Walk up from this file
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "config" / "defaults.toml").exists():
            return parent

    # Fallback
    return Path("/Users/rr/aegis-v5")


ROOT = _resolve_root()
CONFIG = ROOT / "config"
DATA = ROOT / "data"
MODELS = DATA / "models"
ARCHIVE = DATA / "archive"
WAL = DATA / "wal"
FILLS = DATA / "fills"
BUS_ARCHIVE = DATA / "bus"
RUST_CORE = ROOT / "rust_core"
PYTHON_BRAIN = ROOT / "python_brain"
SCRIPTS = ROOT / "scripts"
DOCS = ROOT / "docs"
OBSERVABILITY = ROOT / "observability"
TESTS = ROOT / "tests"
SCHEMAS = ROOT / "schemas"


# Specific frequently-used paths
CONTRACTS_TOML = CONFIG / "contracts.toml"
DEFAULTS_TOML = CONFIG / "defaults.toml"
LEARNED_TOML = CONFIG / "learned.toml"
BOUNDS_TOML = CONFIG / "bounds.toml"
META_LABELER_PKL = MODELS / "meta_labeler.pkl"
FILLS_CLOSED_JSONL = BUS_ARCHIVE / "fills.closed.jsonl"


def ensure_dirs():
    """Create all expected directories if missing."""
    for d in [DATA, MODELS, ARCHIVE, WAL, FILLS, BUS_ARCHIVE]:
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"V5_ROOT: {ROOT}")
    for name, p in [
        ("CONFIG", CONFIG), ("DATA", DATA), ("MODELS", MODELS),
        ("RUST_CORE", RUST_CORE), ("DOCS", DOCS),
    ]:
        print(f"  {name}: {p} (exists={p.exists()})")
    print(f"\nContracts: {CONTRACTS_TOML} ({'found' if CONTRACTS_TOML.exists() else 'missing'})")
    print(f"Meta-labeler: {META_LABELER_PKL} ({'found' if META_LABELER_PKL.exists() else 'missing'})")
