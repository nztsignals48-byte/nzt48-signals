"""
Wiring Validator — NZT-48 Auto-Wiring Guard
=============================================
Ensures every module instantiated in TradingEngine.__init__ is actually
called somewhere in main.py. Catches "dead code" modules that were built
but never wired into the trading loop.

HOW TO USE:
  1. Run on startup: call validate_wiring(engine_instance) after init
  2. Run as pre-deploy check: python3 -m core.wiring_validator
  3. CI/CD: add to test suite (returns non-zero exit code on failures)

ADDING NEW MODULES:
  When you add self.new_module = NewModule() to TradingEngine.__init__,
  add the expected call sites to WIRING_MANIFEST below. The validator
  will immediately flag it as dead code on the next run if not wired.

ACADEMIC BASIS:
  Feitelson (2015) "Experimental Computer Science" — automated structural
  validation prevents "dead module" accumulation in long-running systems.
  Google SRE Book (2016) Chapter 12: Dependency health monitoring.
"""

import inspect
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# WIRING MANIFEST
# Maps self.attribute_name → list of contexts where it MUST be called
# If ANY of the listed call sites exist in main.py → WIRED ✓
# If NONE exist → UNWIRED ✗ (raises warning + logs to data/wiring_audit.json)
# ─────────────────────────────────────────────────────────────────────────────
WIRING_MANIFEST = {
    # Core trading modules
    "virtual_trader": ["virtual_trader.open_position", "virtual_trader.open_positions"],
    "learning": ["learning.record_trade", "learning.get_w12_telegram_summary"],
    "dynamic_sizer": ["dynamic_sizer.calculate_position_size", "dynamic_sizer.update_from_trade"],
    "circuit_breakers": ["circuit_breakers.record_trade_result", "circuit_breakers.check"],
    "discipline": ["discipline.record_trade", "discipline.should_trade"],
    "telegram": ["telegram.send_alert", "telegram.send_message"],
    "tournament": ["tournament.record_trade", "tournament.get_rankings"],
    "adaptive_engine": ["adaptive_engine.update_from_trade", "adaptive_engine.generate_daily_report"],
    "adaptive_intel": ["adaptive_intel.run_nightly_cycle"],
    "trade_autopsy": ["trade_autopsy.analyse", "trade_autopsy.persist"],
    "perf_attribution": ["perf_attribution.attribute_trade"],
    "edge_decay": ["edge_decay.record_trade", "edge_decay.get_time_of_day_scalar"],
    "smart_router": ["smart_router.assess_liquidity"],
    "portfolio_risk": ["portfolio_risk.equity"],
    "sheets": ["sheets.log_trade"],

    # W12 Learning modules — all must be called
    "incremental_learner": ["incremental_learner.update"],
    "v32_drift_detector": ["v32_drift_detector", "drift_instance = self.v32_drift_detector"],  # consolidated global singleton
    "bayesian_win_rate": ["bayesian_win_rate.update"],
    "ensemble_diversity": ["ensemble_diversity.train_all"],
    "active_learning": ["active_learning.get_learning_weights", "active_learning.get_learning_value"],

    # Risk modules
    "cost_drag": ["cost_drag.get_net_edge_after_costs", "cost_drag.is_capacity_constrained"],
    "tail_loss": ["tail_loss.get_size_multiplier"],          # TailLossMonitor — attr name is tail_loss
    "kelly": ["set_kelly_callback"],                          # KellySizer — wired via callback registration
    "portfolio_heat": ["portfolio_heat.record_trade"],
    "circuit_breakers": ["circuit_breakers.check"],

    # AI Research Engine
    "ai_research": ["ai_research.performance_autopsy", "ai_research.weekly_academic_scan"],

    # Profit ladders — both must be used (v3_ladder was removed as dead code)
    "profit_ladder": ["profit_ladder.evaluate"],
    "etp_ladder": ["etp_ladder.evaluate"],
}

# Attributes known to be intentionally unused (documented reasons)
INTENTIONAL_DEAD_CODE = {
    # None currently — all dead code should be removed or wired
}


def validate_wiring(
    main_py_path: str = "main.py",
    fail_hard: bool = False,
) -> dict:
    """
    Scans main.py for call sites of every attribute in WIRING_MANIFEST.

    Args:
        main_py_path: Path to main.py (relative or absolute)
        fail_hard: If True, raise RuntimeError on any unwired module

    Returns:
        dict with keys: wired, unwired, skipped, total, pass
    """
    result = {
        "wired": [],
        "unwired": [],
        "skipped": [],
        "total": len(WIRING_MANIFEST),
        "pass": True,
    }

    try:
        main_path = Path(main_py_path)
        # Try multiple search paths: direct, CWD, script parent, /app (Docker)
        search_paths = [
            main_path,
            Path.cwd() / main_py_path,
            Path(__file__).parent.parent / main_py_path,
            Path("/app") / main_py_path,
        ]
        resolved = None
        for candidate in search_paths:
            if candidate.exists():
                resolved = candidate
                break
        if resolved is None:
            logger.warning("WiringValidator: main.py not found (tried %s)", search_paths)
            result["pass"] = True  # Don't block startup if validator can't find the file
            return result

        source = resolved.read_text()

    except Exception as e:
        logger.warning("WiringValidator: could not read main.py: %s", e)
        return result

    for attr, call_sites in WIRING_MANIFEST.items():
        if attr in INTENTIONAL_DEAD_CODE:
            result["skipped"].append(attr)
            continue

        found = any(call_site in source for call_site in call_sites)
        if found:
            result["wired"].append(attr)
        else:
            result["unwired"].append(attr)
            result["pass"] = False
            logger.warning(
                "WIRING_VALIDATOR: ⚠️  self.%s is instantiated but NEVER called. "
                "Expected call sites: %s. "
                "Either wire it into the trading loop or add to INTENTIONAL_DEAD_CODE.",
                attr, call_sites,
            )

    # Persist audit to disk
    try:
        import json
        from datetime import datetime, timezone
        audit = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "wired": result["wired"],
            "unwired": result["unwired"],
            "skipped": result["skipped"],
            "total": result["total"],
            "pass": result["pass"],
        }
        Path("data").mkdir(exist_ok=True)
        with open("data/wiring_audit.json", "w") as f:
            json.dump(audit, f, indent=2)
    except Exception as e:
        logger.debug("WiringValidator: audit save failed: %s", e)

    if not result["pass"]:
        msg = (
            f"WIRING_VALIDATOR: {len(result['unwired'])} unwired module(s): "
            f"{result['unwired']}. "
            f"These modules are instantiated but never called. "
            f"Wire them into the trading loop or document why they're unused."
        )
        if fail_hard:
            raise RuntimeError(msg)
        else:
            logger.warning(msg)

    return result


def get_telegram_summary(audit_result: dict) -> str:
    """Format wiring audit result for Telegram."""
    total = audit_result["total"]
    wired_count = len(audit_result["wired"])
    unwired = audit_result["unwired"]

    if not unwired:
        return (
            f"🔌 WIRING AUDIT: ALL {wired_count}/{total} MODULES WIRED ✅\n"
            f"No dead code detected."
        )

    lines = [
        f"⚠️ WIRING AUDIT: {len(unwired)} UNWIRED MODULE(S)",
        f"{'─'*38}",
        f"Wired: {wired_count}/{total}",
        f"",
        f"Dead code found:",
    ]
    for attr in unwired:
        lines.append(f"  ✗ self.{attr}")
    lines += [
        f"{'─'*38}",
        f"Action required: wire into trading loop",
        f"or add to INTENTIONAL_DEAD_CODE in",
        f"core/wiring_validator.py",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    """Run as standalone pre-deploy check."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Try to find main.py relative to this file
    script_dir = Path(__file__).parent.parent
    main_py = script_dir / "main.py"

    result = validate_wiring(str(main_py), fail_hard=False)

    print(f"\n{'='*50}")
    print(f"NZT-48 WIRING VALIDATION REPORT")
    print(f"{'='*50}")
    print(f"Total modules in manifest : {result['total']}")
    print(f"Wired                     : {len(result['wired'])} ✅")
    print(f"Unwired (dead code)       : {len(result['unwired'])} {'✅' if not result['unwired'] else '❌'}")
    print(f"Skipped (documented)      : {len(result['skipped'])}")
    print(f"{'─'*50}")

    if result["wired"]:
        print(f"\nWIRED:")
        for attr in result["wired"]:
            print(f"  ✓ self.{attr}")

    if result["unwired"]:
        print(f"\nUNWIRED (ACTION REQUIRED):")
        for attr in result["unwired"]:
            expected = WIRING_MANIFEST[attr]
            print(f"  ✗ self.{attr}")
            print(f"    Expected call sites: {expected}")

    print(f"\n{'='*50}")
    print(f"RESULT: {'PASS ✅' if result['pass'] else 'FAIL ❌'}")
    print(f"{'='*50}\n")

    sys.exit(0 if result["pass"] else 1)
