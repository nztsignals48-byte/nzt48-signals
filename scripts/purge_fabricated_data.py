#!/usr/bin/env python3
"""
AEGIS 0-02: Purge Fabricated Training Data from outcomes.jsonl

Removes:
  1. All records with signal_id starting with "SIG-BF" (backfill / synthetic)
  2. All records with tickers NOT ending in ".L" (non-ISA universe, e.g. US tickers)

Reads from data/outcomes_raw_backup.jsonl (the archived original).
Writes cleaned data to data/outcomes.jsonl.
"""

import json
import sys
from pathlib import Path
from collections import Counter

PROJECT = Path(__file__).resolve().parent.parent
BACKUP = PROJECT / "data" / "outcomes_raw_backup.jsonl"
OUTPUT = PROJECT / "data" / "outcomes.jsonl"


def main():
    if not BACKUP.exists():
        print(f"ERROR: backup not found at {BACKUP}", file=sys.stderr)
        sys.exit(1)

    total = 0
    kept = 0
    purged_bf = 0
    purged_non_isa = 0
    purged_both = 0  # SIG-BF AND non-ISA

    kept_lines = []
    ticker_counts_kept = Counter()
    ticker_counts_purged = Counter()
    bf_tickers = Counter()
    non_isa_tickers = Counter()

    with open(BACKUP, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            rec = json.loads(line)
            sig_id = rec.get("signal_id", "")
            ticker = rec.get("ticker", "")

            is_bf = sig_id.startswith("SIG-BF")
            is_non_isa = not ticker.endswith(".L")

            if is_bf and is_non_isa:
                purged_both += 1
                bf_tickers[ticker] += 1
                non_isa_tickers[ticker] += 1
                ticker_counts_purged[ticker] += 1
            elif is_bf:
                purged_bf += 1
                bf_tickers[ticker] += 1
                ticker_counts_purged[ticker] += 1
            elif is_non_isa:
                purged_non_isa += 1
                non_isa_tickers[ticker] += 1
                ticker_counts_purged[ticker] += 1
            else:
                kept += 1
                kept_lines.append(line)
                ticker_counts_kept[ticker] += 1

    # Write cleaned file
    with open(OUTPUT, "w") as f:
        for line in kept_lines:
            f.write(line + "\n")

    total_purged = purged_bf + purged_non_isa + purged_both

    # Report
    print("=" * 65)
    print("AEGIS 0-02: Purge Fabricated Training Data -- REPORT")
    print("=" * 65)
    print()
    print(f"  Source:   {BACKUP}")
    print(f"  Output:   {OUTPUT}")
    print()
    print(f"  Total records in original:  {total:>6}")
    print(f"  Records KEPT:               {kept:>6}  ({kept/total*100:.1f}%)")
    print(f"  Records PURGED:             {total_purged:>6}  ({total_purged/total*100:.1f}%)")
    print()
    print("  --- Purge breakdown ---")
    print(f"  SIG-BF (backfill/synthetic):     {purged_bf:>6}")
    print(f"  Non-ISA ticker (not .L):         {purged_non_isa:>6}")
    print(f"  Both SIG-BF AND non-ISA:         {purged_both:>6}")
    print(f"  Total purged:                    {total_purged:>6}")
    print()

    if bf_tickers:
        print("  --- SIG-BF records by ticker ---")
        for t, c in sorted(bf_tickers.items(), key=lambda x: -x[1]):
            print(f"    {t:<15} {c:>5} records")
        print()

    if non_isa_tickers:
        print("  --- Non-ISA (non-.L) tickers purged ---")
        for t, c in sorted(non_isa_tickers.items(), key=lambda x: -x[1]):
            print(f"    {t:<15} {c:>5} records")
        print()

    print("  --- Kept records by ticker ---")
    for t, c in sorted(ticker_counts_kept.items(), key=lambda x: -x[1]):
        print(f"    {t:<15} {c:>5} records")
    print()
    print("=" * 65)
    print(f"DONE. Cleaned file written: {OUTPUT} ({kept} records)")
    print("=" * 65)


if __name__ == "__main__":
    main()
