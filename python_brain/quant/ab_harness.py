"""A/B harness — logs parallel decisions for every optional component.

Both arms fire decisions; default trading uses Arm A (or both OR'd).
Outcomes logged to disk for nightly analysis of per-arm alpha uplift.

Consumed by Ouroboros nightly to measure each component's actual contribution.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Decision:
    accept: bool
    confidence: float = 0.5
    rationale: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ABEntry:
    ts: float
    signal_id: str
    name: str                           # component being A/B'd
    arm_a: dict                         # Decision as dict
    arm_b: dict                         # Decision as dict
    features_hash: str = ""
    realized_pnl_bps: float | None = None    # filled in by outcome matcher
    trade_filled: bool = False


class ABHarness:
    """Parallel A/B logger for trading gates.

    Usage:
        harness = ABHarness("meta_labeler", arm_a_fn=ml_classifier, arm_b_fn=rule_filter)
        decision_a, decision_b = harness.evaluate(signal_features)
        # Trade on decision_a (the wanted arm); log both for comparison
    """

    LOG_DIR = Path("/Users/rr/aegis-v5/data/ab_harness")

    def __init__(
        self,
        name: str,
        arm_a_fn: Callable[[Any], Decision],
        arm_b_fn: Callable[[Any], Decision],
        default_arm: str = "A",
    ):
        self.name = name
        self.arm_a_fn = arm_a_fn
        self.arm_b_fn = arm_b_fn
        self.default_arm = default_arm
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._log_path = self.LOG_DIR / f"{name}.jsonl"

    def evaluate(self, signal_features: dict) -> tuple[Decision, Decision]:
        """Run both arms, log, return both."""
        try:
            decision_a = self.arm_a_fn(signal_features)
        except Exception as e:
            decision_a = Decision(accept=False, rationale=f"arm_a error: {e}")
        try:
            decision_b = self.arm_b_fn(signal_features)
        except Exception as e:
            decision_b = Decision(accept=False, rationale=f"arm_b error: {e}")

        entry = ABEntry(
            ts=time.time(),
            signal_id=str(signal_features.get("signal_id", "")),
            name=self.name,
            arm_a=asdict(decision_a) if isinstance(decision_a, Decision) else dict(decision_a),
            arm_b=asdict(decision_b) if isinstance(decision_b, Decision) else dict(decision_b),
        )
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
        except Exception:
            pass

        return decision_a, decision_b

    def default_decision(self, decision_a: Decision, decision_b: Decision) -> Decision:
        return decision_a if self.default_arm == "A" else decision_b


def analyze_log(name: str, realized_outcomes: dict[str, float]) -> dict:
    """Read A/B log, match with realized outcomes, compute uplift.

    realized_outcomes: {signal_id -> realized_pnl_bps}
    """
    log_path = ABHarness.LOG_DIR / f"{name}.jsonl"
    if not log_path.exists():
        return {"name": name, "total": 0}

    entries = []
    with open(log_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                continue

    def arm_pnl(arm_key: str) -> list[float]:
        pnls = []
        for e in entries:
            sid = e.get("signal_id")
            d = e.get(arm_key, {})
            if d.get("accept") and sid in realized_outcomes:
                pnls.append(realized_outcomes[sid])
        return pnls

    a_pnls = arm_pnl("arm_a")
    b_pnls = arm_pnl("arm_b")
    return {
        "name": name,
        "total": len(entries),
        "arm_a_trades": len(a_pnls),
        "arm_b_trades": len(b_pnls),
        "arm_a_mean_bps": sum(a_pnls) / len(a_pnls) if a_pnls else 0.0,
        "arm_b_mean_bps": sum(b_pnls) / len(b_pnls) if b_pnls else 0.0,
        "uplift_bps": (sum(a_pnls) / len(a_pnls) if a_pnls else 0.0)
                      - (sum(b_pnls) / len(b_pnls) if b_pnls else 0.0),
    }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        def arm_ml(features):
            return Decision(accept=features.get("conv", 0) > 0.6, confidence=0.7)

        def arm_rule(features):
            return Decision(accept=features.get("edge_bps", 0) > 5, confidence=0.5)

        h = ABHarness("meta_labeler_ab", arm_ml, arm_rule)
        for i in range(5):
            a, b = h.evaluate({"signal_id": f"sig_{i}", "conv": 0.65, "edge_bps": 7})
            print(f"sig_{i}: A={a.accept} B={b.accept}")

        outcomes = {f"sig_{i}": 10.0 if i < 3 else -5.0 for i in range(5)}
        r = analyze_log("meta_labeler_ab", outcomes)
        print(f"Analysis: {r}")
        print("OK")
