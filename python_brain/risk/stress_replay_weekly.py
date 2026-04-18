"""Weekly stress replay — simulates 2008 / 2020-03 / 2018-Feb shocks on current portfolio.

Takes current position weights, applies historical stress scenarios, reports
projected drawdown. If projected DD > kill threshold, publishes risk.stress_alert.

Runs weekly Sunday 00:00 UTC via supervisor scheduler.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


log = logging.getLogger("stress")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

REPORT_PATH = Path("/Users/rr/aegis-v5/data/stress_reports")


# Historical shock scenarios — daily return patterns (approximate, public data)
STRESS_SCENARIOS = {
    "2008_oct": {
        "description": "2008 Oct 6-10 Lehman fallout",
        "daily_returns": [-0.035, -0.055, -0.016, -0.076, -0.013],
        "equity_beta_expected": 1.0,
    },
    "2020_mar_covid": {
        "description": "2020 Mar 9-16 COVID panic",
        "daily_returns": [-0.076, -0.048, -0.049, -0.097, -0.030, -0.120],
        "equity_beta_expected": 1.0,
    },
    "2018_feb_volmageddon": {
        "description": "2018 Feb 2-8 vol explosion",
        "daily_returns": [-0.021, -0.041, 0.015, -0.034, -0.038],
        "equity_beta_expected": 1.0,
    },
    "2022_jun_bear": {
        "description": "2022 Jun 10-17 rate-shock bear",
        "daily_returns": [-0.029, -0.039, -0.009, 0.014, -0.033],
        "equity_beta_expected": 0.9,
    },
    "flash_crash_2010": {
        "description": "2010 May 6 flash crash single-day",
        "daily_returns": [-0.062],
        "equity_beta_expected": 1.1,
    },
}


@dataclass
class StressResult:
    scenario: str
    initial_equity: float
    final_equity: float
    max_drawdown_pct: float
    breach_kill_threshold: bool
    path: list


def run_scenario(
    positions: dict[str, float],   # symbol -> USD value
    equity: float,
    scenario_name: str,
    kill_dd: float = 0.08,
    avg_beta: float = 1.0,
) -> StressResult:
    sc = STRESS_SCENARIOS.get(scenario_name)
    if sc is None:
        return StressResult(scenario_name, equity, equity, 0.0, False, [])

    # Assume all equity positions move with market * beta
    pos_value = sum(positions.values())
    hedge_value = sum(v for k, v in positions.items() if k in ("SH", "PSQ", "SDS", "QID"))
    net_long = pos_value - 2 * hedge_value  # hedges offset

    equity_series = [equity]
    running = equity
    peak = equity
    max_dd = 0.0
    path = []

    for day_ret in sc["daily_returns"]:
        # Net long portfolio moves with market; hedges move inversely
        pnl_long = net_long * day_ret * avg_beta
        # SDS/QID are 2x; SH/PSQ are 1x
        hedge_multiplier = 1.0
        for k, v in positions.items():
            if k in ("SDS", "QID"):
                pnl_long -= v * day_ret * 2  # inverse 2x
            elif k in ("SH", "PSQ"):
                pnl_long -= v * day_ret       # inverse 1x
        running += pnl_long
        peak = max(peak, running)
        dd = (peak - running) / max(peak, 1e-9)
        max_dd = max(max_dd, dd)
        equity_series.append(running)
        path.append({"pnl": pnl_long, "running": running, "dd": dd})

    return StressResult(
        scenario=scenario_name,
        initial_equity=equity,
        final_equity=running,
        max_drawdown_pct=float(max_dd),
        breach_kill_threshold=max_dd > kill_dd,
        path=path,
    )


def run_all(positions: dict[str, float], equity: float) -> dict:
    results = {}
    for name in STRESS_SCENARIOS:
        results[name] = run_scenario(positions, equity, name)

    REPORT_PATH.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    report = {
        "ts": time.time(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": equity,
        "positions": positions,
        "scenarios": {
            k: {
                "description": STRESS_SCENARIOS[k]["description"],
                "max_drawdown_pct": r.max_drawdown_pct,
                "final_equity": r.final_equity,
                "breach_kill": r.breach_kill_threshold,
            }
            for k, r in results.items()
        },
    }
    report_path = REPORT_PATH / f"stress_{report['date']}.json"
    report_path.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        positions = {"AAPL": 3000, "MSFT": 2500, "NVDA": 2000, "SH": 500}
        equity = 10000
        report = run_all(positions, equity)
        for name, res in report["scenarios"].items():
            flag = "BREACH" if res["breach_kill"] else "ok"
            print(f"{name:30s}: dd={res['max_drawdown_pct']:.2%} [{flag}]")
        print("OK")
