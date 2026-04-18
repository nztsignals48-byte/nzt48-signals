"""Standalone launcher for the capital bandit daemon (used by supervisor)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))

from python_brain.quant.capital_bandit import run_daemon


if __name__ == "__main__":
    asyncio.run(run_daemon())
