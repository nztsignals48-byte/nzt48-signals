"""Sprint S17: SDE Flash Crash Test Generator.

Uses Claude to generate standalone Python scripts for synthetic market data
using Geometric Brownian Motion + Merton jump-diffusion. Generated scripts
are saved to /app/data/sde_tests/ for human review before execution.

Scenario library: flash_crash, slow_bleed, gap_open, vix_spike, whipsaw.

Usage:
  python3 -m python_brain.ouroboros.sde_generator --scenario flash_crash
  python3 -m python_brain.ouroboros.sde_generator --scenario slow_bleed --duration 3600
  python3 -m python_brain.ouroboros.sde_generator --list-scenarios
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from python_brain.ouroboros.claude_helper import claude_query

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
SDE_OUTPUT_DIR = DATA_DIR / "sde_tests"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SDE-Generator] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sde_generator")

# ---------------------------------------------------------------------------
# Scenario library
# ---------------------------------------------------------------------------
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "flash_crash": {
        "name": "Flash Crash",
        "description": "Sudden 5-10% drop in <60 seconds, partial recovery over 5-15 minutes",
        "params": {
            "initial_price": 100.0,
            "drift_mu": 0.0,
            "volatility_sigma": 0.02,
            "jump_intensity_lambda": 0.5,
            "jump_mean": -0.08,
            "jump_std": 0.03,
            "crash_time_frac": 0.3,
            "recovery_frac": 0.6,
            "duration_seconds": 1800,
            "dt_seconds": 1,
        },
    },
    "slow_bleed": {
        "name": "Slow Bleed",
        "description": "Gradual 3-5% decline over 2-4 hours with low volatility",
        "params": {
            "initial_price": 100.0,
            "drift_mu": -0.0003,
            "volatility_sigma": 0.005,
            "jump_intensity_lambda": 0.01,
            "jump_mean": -0.005,
            "jump_std": 0.002,
            "duration_seconds": 7200,
            "dt_seconds": 5,
        },
    },
    "gap_open": {
        "name": "Gap Open",
        "description": "3-5% gap at open followed by mean reversion or continuation",
        "params": {
            "initial_price": 100.0,
            "gap_pct": 0.04,
            "drift_mu": -0.0001,
            "volatility_sigma": 0.015,
            "jump_intensity_lambda": 0.05,
            "jump_mean": 0.0,
            "jump_std": 0.01,
            "duration_seconds": 3600,
            "dt_seconds": 1,
        },
    },
    "vix_spike": {
        "name": "VIX Spike",
        "description": "Volatility regime change: vol doubles over 30min, prices whipsaw",
        "params": {
            "initial_price": 100.0,
            "drift_mu": 0.0,
            "volatility_sigma_base": 0.01,
            "volatility_sigma_spike": 0.03,
            "spike_onset_frac": 0.2,
            "spike_duration_frac": 0.3,
            "jump_intensity_lambda": 0.1,
            "jump_mean": 0.0,
            "jump_std": 0.02,
            "duration_seconds": 3600,
            "dt_seconds": 1,
        },
    },
    "whipsaw": {
        "name": "Whipsaw",
        "description": "Rapid direction changes: +2%, -3%, +1.5% within 30 minutes",
        "params": {
            "initial_price": 100.0,
            "drift_mu": 0.0,
            "volatility_sigma": 0.02,
            "jump_intensity_lambda": 0.3,
            "jump_mean": 0.0,
            "jump_std": 0.025,
            "reversal_count": 4,
            "duration_seconds": 1800,
            "dt_seconds": 1,
        },
    },
}


# ---------------------------------------------------------------------------
# Claude script generation
# ---------------------------------------------------------------------------
def build_sde_prompt(
    scenario_name: str,
    scenario: Dict[str, Any],
    duration_override: Optional[int] = None,
) -> str:
    """Build prompt for Claude to generate an SDE simulation script."""
    params = dict(scenario["params"])
    if duration_override:
        params["duration_seconds"] = duration_override

    params_str = json.dumps(params, indent=2)

    return f"""Generate a standalone Python script that simulates synthetic market tick data using
Stochastic Differential Equations. The script must be self-contained (only numpy and scipy imports).

SCENARIO: {scenario['name']}
DESCRIPTION: {scenario['description']}

PARAMETERS:
{params_str}

REQUIREMENTS:
1. Use Geometric Brownian Motion as the base process:
   dS = mu*S*dt + sigma*S*dW
2. Add Merton jump-diffusion for discontinuities:
   dJ = J_size * dN(lambda) where J_size ~ N(jump_mean, jump_std)
3. Output format: NDJSON with one line per tick:
   {{"timestamp_ns": <int>, "price": <float>, "volume": <int>, "bid": <float>, "ask": <float>}}
4. Simulate realistic bid-ask spread (0.02-0.10% of price)
5. Simulate realistic volume profile (higher at open/close, lower midday)
6. Set random seed for reproducibility (seed=42)
7. Write output to /output/scenario_{scenario_name}.ndjson
8. Print summary statistics at the end: min_price, max_price, final_price, total_ticks, max_drawdown
9. The script must run with: python3 script.py (no arguments required)
10. Include the scenario name and parameters as comments at the top

For the {scenario['name']} scenario specifically:
{scenario['description']}

Return ONLY the Python script as a JSON object:
{{
  "status": "ok",
  "script": "<full Python script as string>",
  "estimated_ticks": <int>,
  "estimated_file_size_kb": <int>
}}"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_generator(
    scenario_name: str,
    duration_override: Optional[int] = None,
) -> int:
    """Generate an SDE simulation script for the given scenario."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    if scenario_name not in SCENARIOS:
        log.error("Unknown scenario: %s. Available: %s", scenario_name, list(SCENARIOS.keys()))
        return 1

    scenario = SCENARIOS[scenario_name]
    log.info("Generating SDE script: %s — %s", scenario["name"], scenario["description"])

    # Query Claude to generate the script
    prompt = build_sde_prompt(scenario_name, scenario, duration_override)
    result = claude_query(prompt)

    if result is None:
        log.error("Claude query failed — no script generated")
        return 1

    script_content = result.get("script", "")
    if not script_content:
        log.error("Claude returned empty script")
        return 1

    # Save the generated script
    SDE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    script_filename = f"sde_{scenario_name}_{timestamp}.py"
    script_path = SDE_OUTPUT_DIR / script_filename

    script_path.write_text(script_content, encoding="utf-8")
    log.info("Generated script saved: %s", script_path)

    # Save metadata alongside
    meta = {
        "scenario": scenario_name,
        "description": scenario["description"],
        "params": scenario["params"],
        "duration_override": duration_override,
        "generated_at": now.isoformat(),
        "script_file": script_filename,
        "estimated_ticks": result.get("estimated_ticks", 0),
        "estimated_file_size_kb": result.get("estimated_file_size_kb", 0),
        "status": "GENERATED_NOT_EXECUTED",
        "review_required": True,
        "run_command": f"docker run --rm -v $(pwd)/data/sde_tests:/output sde-sandbox /output/{script_filename}",
    }
    meta_path = SDE_OUTPUT_DIR / f"sde_{scenario_name}_{timestamp}_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    log.info("Metadata saved: %s", meta_path)

    log.info(
        "Script generated successfully. HUMAN REVIEW REQUIRED before execution.\n"
        "  To run in sandbox:\n"
        "    docker build -f Dockerfile.sde-sandbox -t sde-sandbox .\n"
        "    docker run --rm -v %s:/output sde-sandbox /output/%s",
        SDE_OUTPUT_DIR, script_filename,
    )

    return 0


def list_scenarios():
    """Print available scenarios."""
    print("Available SDE scenarios:")
    print()
    for name, scenario in SCENARIOS.items():
        params = scenario["params"]
        duration = params.get("duration_seconds", 0)
        dt = params.get("dt_seconds", 1)
        est_ticks = duration // dt if dt > 0 else 0
        print(f"  {name:15s}  {scenario['name']}")
        print(f"                   {scenario['description']}")
        print(f"                   Duration: {duration}s, ~{est_ticks} ticks")
        print()


def main():
    parser = argparse.ArgumentParser(description="SDE Flash Crash Test Generator (Sprint S17)")
    parser.add_argument("--scenario", type=str, help="Scenario name (e.g., flash_crash)")
    parser.add_argument("--duration", type=int, help="Override duration in seconds")
    parser.add_argument("--list-scenarios", action="store_true", help="List available scenarios")
    args = parser.parse_args()

    if args.list_scenarios:
        list_scenarios()
        sys.exit(0)

    if not args.scenario:
        parser.error("--scenario is required (use --list-scenarios to see options)")

    try:
        sys.exit(run_generator(args.scenario, args.duration))
    except Exception as e:
        log.error("SDE generator crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
