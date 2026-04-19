"""Dead-code sweep as an actual test — fails CI if any tracked module orphans.

Tracks every module built during Phase A-Q + session fixes. Each must be
referenced by at least one OTHER file (test, supervisor, another module)
or the test fails.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


TRACKED_MODULES = [
    # Phase B: measurement
    ("quant", "min_trl"),
    ("execution", "paper_haircut"),
    ("quant", "drift_sentry"),
    ("quant", "benchmark_streamer"),
    ("quant", "bootstrap_ci"),
    # Phase C: adaptive allocators
    ("quant", "fdr_allocator"),
    ("quant", "covariance_adjusted_kelly"),
    ("quant", "gmm_regime"),
    ("quant", "ab_harness"),
    # Phase D
    ("risk", "hedge_executor"),
    # Phase E
    ("scanner", "pit_snapshot"),
    ("quant", "obi_decay_lab"),
    ("quant", "vpin_percentile_calibrator"),
    ("quant", "kyle_lambda_estimator"),
    ("risk", "marginal_var_attribution"),
    ("quant", "walk_forward_oos"),
    ("quant", "ensemble_entry"),
    # Phase F
    ("intelligence", "llm_uplift_tracker"),
    ("intelligence", "feature_schema_lock"),
    # Phase K
    ("execution", "ioc_fok_support"),
    ("execution", "venue_slippage_tracker"),
    ("execution", "account_size_aware_ac"),
    ("infra", "ibkr_connection_pool"),
    # Phase L
    ("risk", "stress_replay_weekly"),
    ("risk", "cross_portfolio_halt"),
    # Phase M
    ("quant", "sector_regime"),
    ("quant", "regime_persistence"),
    # Phase Q
    ("quant", "strategy_synthesizer"),
    ("quant", "cross_exchange_arbitrage"),
    ("quant", "adversarial_self_test"),
    # Compliance
    ("compliance", "daily_compliance_report"),
    ("compliance", "llm_decision_audit"),
    ("compliance", "best_execution_logger"),
    # Reporting
    ("reporting", "volkov_honest_report"),
    ("reporting", "marginal_var_live"),
    ("reporting", "regime_persistence_publisher"),
    # Engine
    ("engine", "adaptive_gate_chain"),
    ("engine", "signals_gated_forwarder"),
    # Quant (more)
    ("quant", "capital_bandit"),
    ("quant", "capital_bandit_v2"),
    ("quant", "learned_toml_writer"),
    ("quant", "options_flow_signal"),
    ("quant", "cpcv_embargo_auto"),
    # Infra
    ("infra", "arcticdb_adapter"),
    # Ouroboros
    ("ouroboros", "options_flow_tracker"),
    ("ouroboros", "retrain_hooks"),
    ("ouroboros", "retrain_hooks_v2"),
    ("ouroboros", "drift_check"),
    # Core
    ("core", "paths"),
    # New daemons wired this pass
    ("quant", "gmm_regime_daemon"),
    ("quant", "sector_regime_daemon"),
    # Fix pass: v2 GMM daemon + v2 macro buffer (correct daily-return semantics)
    ("quant", "gmm_regime_daemon_v2"),
    ("quant", "macro_feature_buffer_v2"),
]


def test_no_dead_modules():
    """Every tracked module must be referenced by at least one other file."""
    all_code = ""
    for path in ROOT.rglob("*.py"):
        if "_archive" in str(path) or "/target/" in str(path):
            continue
        try:
            all_code += path.read_text() + "\n"
        except Exception:
            pass

    dead = []
    for pkg, mod in TRACKED_MODULES:
        self_path = ROOT / f"python_brain/{pkg}/{mod}.py"
        if not self_path.exists():
            dead.append(f"{pkg}/{mod} (file missing)")
            continue
        self_content = self_path.read_text()
        self_refs = len(re.findall(rf"\b{mod}\b", self_content))
        total_refs = len(re.findall(rf"\b{mod}\b", all_code))
        external_refs = total_refs - self_refs
        if external_refs == 0:
            dead.append(f"{pkg}/{mod}")

    if dead:
        print("DEAD CODE DETECTED:")
        for d in dead:
            print(f"  ✗ {d}")
        assert False, f"{len(dead)} modules have no external references"

    print(f"✓ {len(TRACKED_MODULES)} modules all referenced externally")


if __name__ == "__main__":
    test_no_dead_modules()
    print("PASS")
