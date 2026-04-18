"""Write learned.toml with mandatory bounds-check against config/bounds.toml."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Tuple

BOUNDS_PATH = Path(os.environ.get("AEGIS_V5_CONFIG", "/Users/rr/aegis-v5/config")) / "bounds.toml"
LEARNED_PATH = Path(os.environ.get("AEGIS_V5_CONFIG", "/Users/rr/aegis-v5/config")) / "learned.toml"


def _load_bounds() -> Dict[str, Tuple[float, float]]:
    """Minimal stdlib TOML reader — expects `[section]` with `min = x` / `max = y`.
    Keeps us stdlib-only on Python 3.9+ until the project adopts 3.11+."""
    if not BOUNDS_PATH.exists():
        return {}
    text = BOUNDS_PATH.read_text()
    out: Dict[str, Tuple[float, float]] = {}
    current: str = ""
    vals: Dict[str, float] = {}
    def flush():
        if current and "min" in vals and "max" in vals:
            out[current] = (vals["min"], vals["max"])
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"\[([^\]]+)\]", s)
        if m:
            flush()
            current = m.group(1)
            vals = {}
            continue
        m = re.match(r"(\w+)\s*=\s*([0-9\.\-eE]+)", s)
        if m and current:
            try:
                vals[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    flush()
    return out


def validate_bounds(candidate: Dict[str, float]) -> Tuple[bool, Dict[str, str]]:
    bounds = _load_bounds()
    refusals: Dict[str, str] = {}
    for k, v in candidate.items():
        if k not in bounds:
            continue
        lo, hi = bounds[k]
        if not (lo <= v <= hi):
            refusals[k] = f"{v} not in [{lo}, {hi}]"
    return (len(refusals) == 0, refusals)


def write_learned(d: Dict[str, float]) -> None:
    LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = "schema_version = 1\n\n" + "\n".join(f"{k} = {v}" for k, v in d.items())
    LEARNED_PATH.write_text(body)
