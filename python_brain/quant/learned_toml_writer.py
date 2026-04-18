"""Idempotent writer for config/learned.toml.

Replaces capital_bandit.write_learned_toml which appends duplicate headers.
This writer strips ALL prior generated content (markers + [bandit] block)
and writes a fresh version.

Called nightly by Ouroboros via retrain_hooks.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

ROOT = Path("/Users/rr/aegis-v5")
LEARNED = ROOT / "config/learned.toml"

MARKER_START = "# === BEGIN BANDIT BLOCK ==="
MARKER_END = "# === END BANDIT BLOCK ==="


def strip_bandit_block(text: str) -> str:
    """Remove any previous bandit block between markers, plus stray duplicates."""
    # Strip old marker-delimited block(s)
    text = re.sub(
        rf"{re.escape(MARKER_START)}.*?{re.escape(MARKER_END)}\n?",
        "",
        text,
        flags=re.DOTALL,
    )
    # Also strip legacy unmarkered [bandit] blocks from capital_bandit v1
    # and duplicate "learned.toml — written by capital_bandit" headers
    lines = text.split("\n")
    out = []
    skip_until_next_section = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"#\s*learned\.toml\s*—\s*written\s+by\s+capital_bandit", stripped):
            skip_until_next_section = True
            continue
        if skip_until_next_section:
            if stripped.startswith("[") and stripped != "[bandit]":
                skip_until_next_section = False
            elif stripped == "[bandit]":
                # Stay in skip mode until next different section
                continue
            elif stripped.startswith("kelly_") or stripped.startswith("# "):
                continue
            else:
                skip_until_next_section = False
                out.append(line)
                continue
        if stripped == "[bandit]":
            skip_until_next_section = True
            continue
        out.append(line)
    return "\n".join(out)


def write_bandit_block(bandit_state: dict, promotable: list[str] | None = None,
                      min_kelly: float = 0.005, max_kelly: float = 0.05) -> None:
    """Idempotently write [bandit] block to learned.toml.

    bandit_state: {strategy: {alpha, beta, n_updates, kelly_sample}}
    """
    promotable = promotable or list(bandit_state.keys())

    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    existing = LEARNED.read_text() if LEARNED.exists() else ""
    cleaned = strip_bandit_block(existing).rstrip()

    block_lines = [
        "",
        MARKER_START,
        f"# ts = {time.time()}",
        f"# min_kelly = {min_kelly}, max_kelly = {max_kelly}",
        "[bandit]",
    ]
    for strat in sorted(bandit_state.keys()):
        info = bandit_state[strat]
        a = info.get("alpha", 1.0)
        b = info.get("beta", 1.0)
        n = info.get("n_updates", 0)
        kelly = info.get("kelly_sample", min_kelly)
        promoted = "promoted" if strat in promotable else "starved"
        block_lines.append(f"# {strat}: alpha={a:.2f} beta={b:.2f} n={n} [{promoted}]")
        block_lines.append(f"kelly_{strat} = {kelly:.4f}")
    block_lines.append(MARKER_END)
    block_lines.append("")

    final = cleaned + "\n" + "\n".join(block_lines) + "\n"
    # Final collapse of multiple blank lines
    final = re.sub(r"\n{3,}", "\n\n", final)
    LEARNED.write_text(final)


def clean_learned_toml() -> int:
    """One-shot: strip ALL bandit-generated cruft. Returns bytes removed."""
    if not LEARNED.exists():
        return 0
    before = LEARNED.read_text()
    after = strip_bandit_block(before)
    after = re.sub(r"\n{3,}", "\n\n", after).rstrip() + "\n"
    LEARNED.write_text(after)
    return len(before) - len(after)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Clean up any drift from testing
        removed = clean_learned_toml()
        print(f"Cleaned {removed} bytes of cruft")

        # Now write a fresh block
        write_bandit_block({
            "good_strat": {"alpha": 10, "beta": 2, "n_updates": 12, "kelly_sample": 0.045},
            "bad_strat":  {"alpha": 2,  "beta": 10, "n_updates": 12, "kelly_sample": 0.008},
        }, promotable=["good_strat"])

        # Verify only 1 bandit block
        text = LEARNED.read_text()
        n_markers = text.count(MARKER_START)
        n_bandit_sections = len(re.findall(r"^\[bandit\]", text, re.MULTILINE))
        n_legacy_headers = len(re.findall(r"written by capital_bandit", text))
        print(f"Start markers: {n_markers}, [bandit] sections: {n_bandit_sections}, "
              f"legacy headers: {n_legacy_headers}")
        assert n_markers == 1, "duplicate markers!"
        assert n_bandit_sections == 1, "duplicate sections!"

        # Write again — should still be 1
        write_bandit_block({"good_strat": {"alpha": 20, "beta": 4, "n_updates": 24, "kelly_sample": 0.045}})
        text = LEARNED.read_text()
        n_markers = text.count(MARKER_START)
        n_bandit_sections = len(re.findall(r"^\[bandit\]", text, re.MULTILINE))
        print(f"After 2nd write: markers={n_markers}, sections={n_bandit_sections}")
        assert n_markers == 1 and n_bandit_sections == 1
        print("OK — idempotent writer works")
