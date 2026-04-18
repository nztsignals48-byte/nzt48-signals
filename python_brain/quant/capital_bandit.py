"""Thompson-sampling capital bandit — allocates Kelly across strategies adaptively.

Each strategy has Beta(alpha, beta) posterior over "cost-adjusted edge > 0"
probability. Nightly update: alpha += wins, beta += losses.

Allocation = normalized draws from posterior × FDR-promotion filter.
Writes per-strategy kelly values to learned.toml; sig2order reads on mtime.

Consumed nightly by ouroboros_v3 + daemon runs continuously.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("bandit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
STATE = ROOT / "data/bandit_state.json"
LEARNED = ROOT / "config/learned.toml"


@dataclass
class BanditState:
    priors: dict[str, tuple[float, float]] = field(default_factory=dict)   # strategy -> (alpha, beta)
    n_updates: dict[str, int] = field(default_factory=dict)


class ThompsonCapitalBandit:
    def __init__(self, default_alpha: float = 1.0, default_beta: float = 1.0,
                 kelly_cap: float = 0.05, min_kelly: float = 0.005):
        self.state = BanditState()
        self.kelly_cap = kelly_cap
        self.min_kelly = min_kelly
        self.default_a = default_alpha
        self.default_b = default_beta
        self._load()

    def _load(self):
        if not STATE.exists():
            return
        try:
            d = json.loads(STATE.read_text())
            self.state.priors = {k: tuple(v) for k, v in d.get("priors", {}).items()}
            self.state.n_updates = d.get("n_updates", {})
        except Exception:
            pass

    def _save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "priors": {k: list(v) for k, v in self.state.priors.items()},
            "n_updates": self.state.n_updates,
            "ts": time.time(),
        }, indent=2))

    def register(self, strategy: str):
        if strategy not in self.state.priors:
            self.state.priors[strategy] = (self.default_a, self.default_b)
            self.state.n_updates[strategy] = 0

    def update(self, strategy: str, net_edge_bps: float):
        """Update Beta posterior: win if net_edge > 0."""
        self.register(strategy)
        a, b = self.state.priors[strategy]
        if net_edge_bps > 0:
            a += 1
        else:
            b += 1
        self.state.priors[strategy] = (a, b)
        self.state.n_updates[strategy] = self.state.n_updates.get(strategy, 0) + 1

    def sample_kelly(self, strategy: str, fdr_promoted: bool = True) -> float:
        """Thompson sample: Kelly proportional to posterior draw."""
        import random
        self.register(strategy)
        a, b = self.state.priors[strategy]
        # Beta draw approximated via gamma ratio
        try:
            sample = random.betavariate(max(a, 0.01), max(b, 0.01))
        except Exception:
            sample = 0.5
        # FDR-gated: non-promoted strategies can only hold current Kelly, not raise
        if not fdr_promoted:
            sample = min(sample, 0.3)
        kelly = self.min_kelly + (self.kelly_cap - self.min_kelly) * sample
        return max(self.min_kelly, min(self.kelly_cap, kelly))

    def write_learned_toml(self, promotable: list[str] | None = None):
        """Emit per-strategy kelly + bandit state to learned.toml."""
        promotable = promotable or list(self.state.priors.keys())
        lines = [
            "# learned.toml — written by capital_bandit",
            f"# ts = {time.time()}",
            "",
            "[bandit]",
        ]
        for strat in sorted(self.state.priors.keys()):
            kelly = self.sample_kelly(strat, fdr_promoted=(strat in promotable))
            a, b = self.state.priors[strat]
            n = self.state.n_updates.get(strat, 0)
            lines.append(f'# {strat}: alpha={a:.2f} beta={b:.2f} n_updates={n}')
            lines.append(f'kelly_{strat} = {kelly:.4f}')
        # Preserve any existing non-bandit entries
        existing_content = ""
        if LEARNED.exists():
            existing_content = LEARNED.read_text()
            # Strip old [bandit] block
            lines_existing = existing_content.split("\n")
            out_lines = []
            skip = False
            for l in lines_existing:
                if l.strip() == "[bandit]":
                    skip = True
                    continue
                if skip and l.strip().startswith("["):
                    skip = False
                if not skip:
                    out_lines.append(l)
            existing_content = "\n".join(out_lines)

        final = (existing_content.rstrip() + "\n\n" + "\n".join(lines)).strip() + "\n"
        LEARNED.parent.mkdir(parents=True, exist_ok=True)
        LEARNED.write_text(final)

    def snapshot(self) -> dict:
        out = {}
        for strat, (a, b) in self.state.priors.items():
            mean = a / (a + b) if (a + b) > 0 else 0.5
            out[strat] = {
                "alpha": a,
                "beta": b,
                "mean_p_win": mean,
                "n_updates": self.state.n_updates.get(strat, 0),
                "kelly_sample": self.sample_kelly(strat),
            }
        return out


# Daemon path — wraps the bandit + consumes orders.filled to update posteriors
async def run_daemon():
    if NATS is None:
        log.error("nats-py required")
        return
    bandit = ThompsonCapitalBandit()
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_fill(msg):
        try:
            d = json.loads(msg.data)
            strat = d.get("strategy_name") or d.get("strategy", "unknown")
            pnl_bps = d.get("realized_pnl_bps")
            if pnl_bps is None:
                pnl = d.get("realized_pnl_gbp", 0)
                pnl_bps = float(pnl)
            bandit.update(strat, float(pnl_bps))
            bandit._save()
        except Exception as e:
            log.warning("fill update fail: %s", e)

    await nc.subscribe("orders.filled", cb=on_fill)
    await nc.subscribe("fills.closed", cb=on_fill)
    log.info("capital bandit daemon listening")

    while True:
        await asyncio.sleep(300)  # 5 min publish
        snap = bandit.snapshot()
        try:
            await nc.publish("bandit.kelly", json.dumps(snap).encode())
        except Exception:
            pass
        # Write to learned.toml hourly
        bandit.write_learned_toml()
        log.info("bandit kellys: %s", {k: round(v["kelly_sample"], 4) for k, v in snap.items()})


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        b = ThompsonCapitalBandit()
        for _ in range(20):
            b.update("good", 3.5)
            b.update("bad", -2.0)
        snap = b.snapshot()
        for s, info in snap.items():
            print(f"{s}: kelly={info['kelly_sample']:.4f} p_win={info['mean_p_win']:.2f} n={info['n_updates']}")
        b.write_learned_toml(promotable=["good"])
        print(f"Wrote {LEARNED}")
        print("OK")
    else:
        asyncio.run(run_daemon())
