"""Capital bandit v2 — uses idempotent learned.toml writer.

Identical to capital_bandit.ThompsonCapitalBandit but wraps with the
learned_toml_writer module so nightly writes don't accumulate duplicate
blocks.

Nightly ouroboros should import from here (not capital_bandit.py) going
forward. The daemon entry in scripts/capital_bandit_daemon_launcher.py
can be swapped to use this class.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from python_brain.quant.capital_bandit import ThompsonCapitalBandit
from python_brain.quant.learned_toml_writer import write_bandit_block, clean_learned_toml


class ThompsonCapitalBanditV2(ThompsonCapitalBandit):
    def write_learned_toml(self, promotable: list[str] | None = None):
        """Idempotent writer — calls learned_toml_writer.write_bandit_block()."""
        snap = self.snapshot()
        write_bandit_block(
            snap,
            promotable=promotable,
            min_kelly=self.min_kelly,
            max_kelly=self.kelly_cap,
        )


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Clean any prior cruft first
        removed = clean_learned_toml()
        print(f"Cleanup: removed {removed} bytes of cruft")

        b = ThompsonCapitalBanditV2()
        for _ in range(10):
            b.update("good_v2", 4.0)
            b.update("bad_v2", -2.0)
        b.write_learned_toml(promotable=["good_v2"])

        # Re-write — should NOT duplicate
        b.write_learned_toml(promotable=["good_v2"])

        from pathlib import Path
        import re
        text = Path("/Users/rr/aegis-v5/config/learned.toml").read_text()
        n_start = text.count("=== BEGIN BANDIT BLOCK ===")
        n_section = len(re.findall(r"^\[bandit\]", text, re.MULTILINE))
        print(f"Markers: {n_start}, [bandit] sections: {n_section}")
        assert n_start == 1 and n_section == 1, "idempotency failed!"
        print("OK")
