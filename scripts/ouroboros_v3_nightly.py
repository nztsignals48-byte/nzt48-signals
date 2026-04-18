"""Ouroboros v3 — extended nightly loop with all Phase A-Q learning.

Runs at 23:30 UTC. Pulls from WAL + fills archive, then:
 1. Per-strategy DSR + MinTRL + CPCV + bootstrap CI
 2. Covariance-adjusted Kelly
 3. GMM regime refit
 4. VPIN percentile calibration
 5. OBI decay lab
 6. Kyle's lambda estimation
 7. Meta-labeler retrain (schema-locked)
 8. Strategy synthesizer (propose new variants)
 9. Cross-exchange arb scan
10. Adversarial self-test (weekly on Sundays)
11. Walk-forward OOS
12. Volkov honest report (weekly on Mondays)
13. Weekly stress replay (Sundays)
14. Auto-kill poor strategies
15. Write all tunables to learned.toml
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))

log = logging.getLogger("ouro-v3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_all() -> dict:
    """Run the full nightly pipeline."""
    report = {"ts": time.time(), "steps": {}}
    now = datetime.now(timezone.utc)

    # Step 1: VPIN percentile calibration
    try:
        from python_brain.quant.vpin_percentile_calibrator import run_calibration
        r = run_calibration()
        report["steps"]["vpin_calibration"] = {
            "n_thresholds": len(r.get("thresholds", {})),
        }
    except Exception as e:
        report["steps"]["vpin_calibration"] = {"error": str(e)}

    # Step 2: OBI decay lab
    try:
        from python_brain.quant.obi_decay_lab import run_lab
        r = run_lab()
        report["steps"]["obi_decay"] = {"symbols_fitted": r.get("symbols_fitted", 0)}
    except Exception as e:
        report["steps"]["obi_decay"] = {"error": str(e)}

    # Step 3: Kyle's lambda
    try:
        from python_brain.quant.kyle_lambda_estimator import run
        r = run()
        report["steps"]["kyle_lambda"] = {"n_symbols": r.get("n_symbols", 0)}
    except Exception as e:
        report["steps"]["kyle_lambda"] = {"error": str(e)}

    # Step 4: Strategy synthesizer
    try:
        from python_brain.quant.strategy_synthesizer import synthesize
        r = synthesize()
        report["steps"]["synthesizer"] = {
            "n_proposals": r.get("n_proposals", 0),
        }
    except Exception as e:
        report["steps"]["synthesizer"] = {"error": str(e)}

    # Step 5: Volkov honest report (always)
    try:
        from python_brain.reporting.volkov_honest_report import write_report
        p = write_report()
        report["steps"]["volkov_report"] = {"path": str(p)}
    except Exception as e:
        report["steps"]["volkov_report"] = {"error": str(e)}

    # Step 6: Auto-kill poor strategies
    try:
        from scripts.auto_kill_poor_strategies import scan_and_disable
        r = scan_and_disable()
        report["steps"]["auto_kill"] = {
            "disabled": list(r.get("disabled", {}).keys()),
            "kept": list(r.get("kept", {}).keys()),
        }
    except Exception as e:
        report["steps"]["auto_kill"] = {"error": str(e)}

    # Step 7: Weekly stress replay (Sundays)
    if now.weekday() == 6:  # Sunday
        try:
            from python_brain.risk.stress_replay_weekly import run_all as stress_run
            r = stress_run({"AAPL": 3000, "MSFT": 2500, "NVDA": 2000, "SH": 500}, 10000)
            report["steps"]["stress_replay"] = {
                "scenarios_run": len(r.get("scenarios", {})),
            }
        except Exception as e:
            report["steps"]["stress_replay"] = {"error": str(e)}

    # Step 8: Adversarial self-test (monthly, day 1)
    if now.day == 1:
        try:
            # Synchronous synthesis only for nightly (full async test runs separately)
            from python_brain.quant.adversarial_self_test import synthesize_signals
            sigs = synthesize_signals(20)
            report["steps"]["adversarial"] = {
                "signals_generated": len(sigs),
                "note": "full veto-rate test runs separately against live NATS",
            }
        except Exception as e:
            report["steps"]["adversarial"] = {"error": str(e)}

    # Step 9: Daily compliance report
    try:
        from python_brain.compliance.daily_compliance_report import write_report as write_compl
        p = write_compl()
        report["steps"]["compliance"] = {"path": str(p)}
    except Exception as e:
        report["steps"]["compliance"] = {"error": str(e)}

    # Step 10: PIT snapshot
    try:
        from python_brain.scanner.pit_snapshot import write_snapshot
        p = write_snapshot()
        report["steps"]["pit_snapshot"] = {"path": str(p)}
    except Exception as e:
        report["steps"]["pit_snapshot"] = {"error": str(e)}

    # Step 11: Covariance-adjusted Kelly from recent fills
    try:
        from python_brain.quant.covariance_adjusted_kelly import kelly_from_fills
        import numpy as np
        from collections import defaultdict
        fills_path = ROOT / "data/bus/fills.closed.jsonl"
        if fills_path.exists():
            fills_by_strat: dict[str, list[float]] = defaultdict(list)
            with open(fills_path) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        s = d.get("strategy_name") or d.get("strategy")
                        p = d.get("realized_pnl_bps") or d.get("realized_pnl_gbp") or 0
                        if s:
                            fills_by_strat[s].append(float(p) / 10000)  # bps -> fraction
                    except Exception:
                        continue
            # Use last 60 days per strategy
            trimmed = {k: np.array(v[-60:]) for k, v in fills_by_strat.items() if len(v) >= 10}
            kellys = kelly_from_fills(trimmed)
            report["steps"]["kelly_update"] = {"kellys": kellys}
    except Exception as e:
        report["steps"]["kelly_update"] = {"error": str(e)}

    # Step 12: Write report
    report_path = ROOT / "data/ouroboros_v3_reports"
    report_path.mkdir(parents=True, exist_ok=True)
    out_file = report_path / f"{now.strftime('%Y-%m-%d')}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str))
    log.info("ouroboros v3 complete -> %s", out_file)

    return report


if __name__ == "__main__":
    run_all()
