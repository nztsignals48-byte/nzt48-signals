#!/usr/bin/env python3
"""Zero-trade-day autodiagnostic.

At session close, if trade_count == 0 AND the exchange was open, run this.
Produces docs/incidents/YYYY-MM-DD.md AND docs/incidents/YYYY-MM-DD.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INC = ROOT / "docs" / "incidents"


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "no-git"


def collect_evidence() -> dict:
    ev: dict = {}
    # (1) data_health
    try:
        from python_brain.core.data_health import DataHealthMonitor
        statuses = DataHealthMonitor().check()
        ev["intel_files_empty"] = [n for n, s in statuses.items() if s.status != "FED"]
        ev["strategies_starved"] = sorted({c for s in statuses.values() for c in s.consumers_starved})
    except Exception as e:
        ev["intel_files_empty"] = []
        ev["strategies_starved"] = []
        ev["data_health_err"] = str(e)
    # (2-6) placeholders — phases 1-11 fill
    ev.update({
        "confidence_floor_rejections": None,
        "conviction_rejections": None,
        "portfolio_rejected_signals": None,
        "ibkr_session_gaps_min": None,
        "ledger_unfilled_count": None,
    })
    return ev


def main() -> int:
    today = date.today().isoformat()
    INC.mkdir(parents=True, exist_ok=True)
    md_path = INC / f"{today}_zero_trade_day.md"
    js_path = INC / f"{today}_zero_trade_day.json"

    evidence = collect_evidence()
    root_cause = "intel_starvation" if evidence.get("intel_files_empty") else "unknown"
    primary = (evidence.get("intel_files_empty") or ["engine"])[0]
    report = {
        "date": today,
        "root_cause": root_cause,
        "primary_subsystem": primary,
        "suspected_change_sha": git_head(),
        "evidence": evidence,
    }
    js_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(
        f"# Zero-Trade-Day Incident — {today}\n\n"
        f"**Root cause:** {root_cause}\n"
        f"**Primary subsystem:** {primary}\n"
        f"**Suspected change:** `{report['suspected_change_sha']}`\n\n"
        f"## Evidence\n```\n{json.dumps(evidence, indent=2)}\n```\n"
        f"\n## Next\nNext session blocked until this file has `resolution:` set and a commit closes it.\n"
    )
    print(f"wrote {md_path}")
    print(f"wrote {js_path}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
