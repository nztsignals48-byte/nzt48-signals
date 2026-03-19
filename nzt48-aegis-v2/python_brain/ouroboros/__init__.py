"""Ouroboros v6.0 — Nightly learning loop, backfill simulation, and universe management.

Lives inside python_brain/ so it can import brain.indicators and brain.strategies
directly. Separate from the top-level ouroboros/ package which handles WAL-based
pipeline analytics.

Entry points:
  python3 -m python_brain.ouroboros                              # menu
  python3 -m python_brain.ouroboros.nightly_v6                   # nightly analysis
  python3 -m python_brain.ouroboros.backfill_simulator            # 7-day backfill sim
  python3 -m python_brain.ouroboros.isa_universe_discovery        # ISA universe discovery
  python3 -m python_brain.ouroboros.universe_refresh              # daily universe refresh
  python3 -m python_brain.ouroboros.ibkr_scanner                  # weekly IBKR full scan
  python3 -m python_brain.ouroboros.ticker_selector               # daily ticker ranking
  python3 -m python_brain.ouroboros.sheets_sync                   # Google Sheets manual sync
"""
