"""Entry point for python3 -m python_brain.ouroboros."""

import sys


def main():
    print("Ouroboros v6.0 — AEGIS V2 Nightly Learning Loop + Universe Management")
    print()
    print("Available commands:")
    print("  python3 -m python_brain.ouroboros.nightly_v6                   # Run nightly analysis")
    print("  python3 -m python_brain.ouroboros.backfill_simulator            # Run 7-day backfill sim")
    print("  python3 -m python_brain.ouroboros.full_universe_builder         # Build 36K+ ticker master list")
    print("  python3 -m python_brain.ouroboros.isa_universe_discovery        # ISA universe discovery (legacy)")
    print("  python3 -m python_brain.ouroboros.universe_refresh              # Daily 500-ticker validation")
    print("  python3 -m python_brain.ouroboros.ibkr_scanner                  # Weekly IBKR full scan")
    print("  python3 -m python_brain.ouroboros.ticker_selector               # Daily tiered ticker ranking")
    print()
    print("Cron schedule (UTC, Mon-Fri):")
    print("  04:50 — nightly_v6 (23:50 ET)")
    print("  06:00 — universe_refresh (daily)")
    print("  06:30 — ticker_selector (daily, after refresh)")
    print("  07:00 — backfill_simulator")
    print()
    print("Cron schedule (UTC, Sunday):")
    print("  22:00 — ibkr_scanner (weekly full scan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
