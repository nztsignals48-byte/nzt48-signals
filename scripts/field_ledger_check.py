#!/usr/bin/env python3
"""CI gate: every non-SHADOW row of FIELD_CONSUMPTION_LEDGER.md must have a proof metric.

Phase 0-10: warn-only. Phase 11 onwards: --strict fails build on any unfilled non-SHADOW row.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "FIELD_CONSUMPTION_LEDGER.md"


def parse_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 5:
            continue
        # skip header / separator
        if cols[0].lower() == "field" or set(cols[0]) <= {"-"}:
            continue
        rows.append({"field": cols[0], "producer": cols[1], "consumer": cols[2], "metric": cols[3], "status": cols[-1]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    text = LEDGER.read_text()
    rows = parse_rows(text)
    unfilled = [r for r in rows if r["status"].startswith("PENDING") or r["status"] == "UNWIRED"]
    shadow = [r for r in rows if r["status"] == "SHADOW"]
    filled = [r for r in rows if r["status"] in {"FILLED", "FED"}]

    print(f"field_ledger: {len(filled)} filled / {len(shadow)} shadow / {len(unfilled)} pending / total {len(rows)}")

    if args.strict and unfilled:
        print("UNFILLED ledger rows in --strict mode:")
        for r in unfilled:
            print(f"  {r['field']:<24} {r['status']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
