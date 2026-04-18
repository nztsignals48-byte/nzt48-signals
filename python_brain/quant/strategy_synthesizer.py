"""Self-healing strategy synthesizer — scans fill WAL for recurring profitable
patterns, proposes new strategy variants, runs them in shadow mode.

Beyond institutional: most shops require human researchers to hand-craft new
strategies. V5 auto-mines recurring patterns from its own WAL and proposes
variants. Promotions gated by FDR + DSR + CPCV same as human-authored
strategies.

Consumed by: Ouroboros (calls synthesize() nightly).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


ROOT = Path("/Users/rr/aegis-v5")
FILLS_WAL = ROOT / "data/bus/fills.closed.jsonl"
OUTPUT = ROOT / "data/synthesized_strategies.json"


@dataclass
class StrategyProposal:
    name: str
    template: str               # source strategy cloned from
    parameter_changes: dict
    rationale: str
    expected_edge_bps: float
    n_supporting_trades: int
    status: str = "shadow"      # shadow / promoted / rejected


def scan_fills(path: Path = FILLS_WAL, limit: int = 5000) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
                if len(out) >= limit:
                    break
    except Exception:
        pass
    return out


def identify_conditional_patterns(fills: list[dict]) -> list[StrategyProposal]:
    """Find (ticker_bucket, regime, session) subsets where strategy X had
    significantly higher PF than overall.

    Proposal: a regime/session/ticker-filtered clone of strategy X.
    """
    if not fills:
        return []

    # Group by (strategy, regime, session); compute PF per bucket
    buckets: dict[tuple, list[float]] = defaultdict(list)
    for f in fills:
        pnl = f.get("realized_pnl_bps") or f.get("realized_pnl_gbp") or 0
        strat = f.get("strategy_name") or f.get("strategy", "unknown")
        regime = f.get("regime", "unknown")
        session = f.get("session", "unknown")
        buckets[(strat, regime, session)].append(float(pnl))

    # Overall PF per strategy
    overall: dict[str, list[float]] = defaultdict(list)
    for (strat, _, _), pnls in buckets.items():
        overall[strat].extend(pnls)

    def pf(pnls: list[float]) -> float:
        wins = sum(p for p in pnls if p > 0)
        losses = -sum(p for p in pnls if p < 0)
        return wins / losses if losses > 0 else (float("inf") if wins > 0 else 1.0)

    proposals = []
    for (strat, regime, session), pnls in buckets.items():
        if len(pnls) < 30:
            continue
        bucket_pf = pf(pnls)
        overall_pf = pf(overall[strat])
        # If bucket PF is significantly higher AND n_trades sufficient → propose variant
        if bucket_pf > overall_pf * 1.5 and bucket_pf > 1.3:
            proposals.append(StrategyProposal(
                name=f"{strat}_{regime}_{session}",
                template=strat,
                parameter_changes={
                    "regime_filter": regime,
                    "session_filter": session,
                    "conviction_boost": 0.1,
                },
                rationale=(
                    f"Bucket PF {bucket_pf:.2f} vs overall {overall_pf:.2f} "
                    f"on {len(pnls)} trades"
                ),
                expected_edge_bps=float(np.mean(pnls)),
                n_supporting_trades=len(pnls),
            ))
    return proposals


def synthesize() -> dict:
    """Run full synthesis cycle."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fills = scan_fills()
    proposals = identify_conditional_patterns(fills)
    result = {
        "n_fills_scanned": len(fills),
        "n_proposals": len(proposals),
        "proposals": [p.__dict__ for p in proposals],
    }
    OUTPUT.write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Synthesize synthetic fills for test
        fills = []
        rng = np.random.default_rng(42)
        for _ in range(500):
            fills.append({
                "strategy_name": "filing_change_detect",
                "regime": rng.choice(["calm", "crisis"]),
                "session": rng.choice(["us_session", "overnight"]),
                "realized_pnl_bps": float(rng.normal(2, 10)),
            })
        # Override FILLS_WAL for test
        test_path = Path("/tmp/test_fills.jsonl")
        with open(test_path, "w") as f:
            for x in fills:
                f.write(json.dumps(x) + "\n")
        loaded = scan_fills(test_path)
        proposals = identify_conditional_patterns(loaded)
        print(f"Proposals: {len(proposals)}")
        for p in proposals[:3]:
            print(f"  {p.name}: edge={p.expected_edge_bps:.2f} bps, n={p.n_supporting_trades}")
        print("OK")
    else:
        synthesize()
