#!/usr/bin/env python3
"""CI gate: every Python module must have a caller.

A module is LIVE if any of these hold:
  1. It's an __init__.py
  2. It's an entry point (server.py, engine/loop.py, scanner/scanner.py)
  3. Its dotted name appears in any import statement anywhere in python_brain/ or tests/
  4. Its basename appears as a word anywhere in tests/ (dynamic-import-safe)
  5. It's referenced in registry.toml

Usage:
    python scripts/dead_code_check.py
    python scripts/dead_code_check.py --strict
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = ROOT / "python_brain"
TESTS = ROOT / "tests"

ENTRY_POINTS = {
    "python_brain/server.py",
    "python_brain/engine/loop.py",
    "python_brain/scanner/scanner.py",
    "python_brain/ouroboros/core.py",
    # V5 standalone NATS services
    "python_brain/engine/ibkr_tick_feed.py",
    "python_brain/engine/signal_to_order_bridge.py",
    "python_brain/engine/exit_to_order_bridge.py",
    "python_brain/engine/compounding_tracker.py",
    "python_brain/engine/metrics_feeder.py",
    "python_brain/engine/paper_executor.py",
    "python_brain/scanner/ibkr_scanner.py",
    "python_brain/scanner/ibkr_scanner_v2.py",
    "python_brain/scanner/universe_rotator.py",
    "python_brain/scanner/universe_rotator_v2.py",
    "python_brain/news/ibkr_news_reactor.py",
    "python_brain/news/llm_news_analyzer.py",
    "python_brain/agents/agent_swarm.py",
    "python_brain/core/nats_archiver.py",
    "python_brain/core/nats_client_live.py",
    "python_brain/core/metrics_http.py",
}


def is_live(module_path: Path) -> bool:
    rel = module_path.relative_to(ROOT)
    rel_str = str(rel)
    if module_path.name == "__init__.py":
        return True
    if rel_str in ENTRY_POINTS:
        return True

    module = rel.with_suffix("")
    dotted = str(module).replace("/", ".")
    basename = module.stem

    patterns = [
        rf"\bimport {re.escape(dotted)}\b",
        rf"\bfrom {re.escape(dotted)}\b",
        rf'["\']{re.escape(dotted)}["\']',
        rf"\b{re.escape(basename)}\b",
    ]

    for root in (PY_ROOT, TESTS):
        for py_file in root.rglob("*.py"):
            if py_file == module_path:
                continue
            try:
                text = py_file.read_text(errors="ignore")
            except Exception:
                continue
            for pat in patterns:
                if re.search(pat, text):
                    return True

    registry = PY_ROOT / "strategies" / "registry.toml"
    if registry.exists() and basename in registry.read_text():
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    orphans: list[str] = []
    for p in PY_ROOT.rglob("*.py"):
        if not is_live(p):
            orphans.append(str(p.relative_to(ROOT)))

    if orphans:
        mode = "strict" if args.strict else "warn"
        print(f"[{mode}] {len(orphans)} orphan module(s):")
        for o in orphans:
            print(f"  {o}")
        if args.strict:
            return 1
    else:
        print("dead_code_check: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
