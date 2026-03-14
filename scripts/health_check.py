#!/usr/bin/env python3
"""
NZT-48 Post-Deploy Health Check
================================
Run after every deploy to verify all critical systems are operational.
Usage: python3 scripts/health_check.py
Exit code 0 = all checks pass, 1 = failures detected
"""
import sys
import os
import json
import time
import sqlite3
import importlib
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

CHECKS_PASSED = 0
CHECKS_FAILED = 0
WARNINGS = 0

def check(name, condition, detail=""):
    global CHECKS_PASSED, CHECKS_FAILED
    if condition:
        print(f"  ✓ {name}")
        CHECKS_PASSED += 1
    else:
        print(f"  ✗ {name} — {detail}")
        CHECKS_FAILED += 1

def warn(name, detail=""):
    global WARNINGS
    print(f"  ⚠ {name} — {detail}")
    WARNINGS += 1

def main():
    print("=" * 60)
    print("NZT-48 POST-DEPLOY HEALTH CHECK")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # 1. CONFIG CHECKS
    print("\n[1] CONFIGURATION")
    try:
        import config as cfg
        config = cfg.load_config()
        check("Config loads", config is not None)
        check("Primary mode is UK_ISA", cfg.get_primary_mode() == "UK_ISA")

        tickers = cfg.get_isa_tickers()
        check(f"ISA tickers loaded ({len(tickers)})", len(tickers) >= 20, f"Only {len(tickers)} tickers")
        check("Paper mode active", cfg.is_paper_mode(), "LIVE MODE DETECTED!")

        # Check no zombie tickers
        zombies = {"3AAL.L", "3AAS.L", "3AZS.L", "3MTA.L", "3MTS.L", "3ORA.L", "SOX3.L"}
        found_zombies = zombies.intersection(set(tickers))
        check("No zombie tickers in universe", len(found_zombies) == 0, f"Found: {found_zombies}")
    except Exception as e:
        check("Config loads", False, str(e))

    # 2. MODULE IMPORTS
    print("\n[2] MODULE IMPORTS")
    critical_modules = [
        "main",
        "config",
        "execution.session_manager",
        "execution.cost_model",
        "bots.kelly_sizer",
        "feeds.data_feeds",
        "core.signal_engine",
        "core.tick_loop",
        "strategies.daily_target",
    ]
    for mod in critical_modules:
        try:
            importlib.import_module(mod)
            check(f"Import {mod}", True)
        except Exception as e:
            check(f"Import {mod}", False, str(e))

    # 3. SESSION PHASE CHECK
    print("\n[3] SESSION PHASE (LSE)")
    try:
        from execution.session_manager import SessionBoundaryManager
        sm = SessionBoundaryManager()

        # Check LSE phase exists and works
        lse_phase = sm.get_current_lse_phase()
        check("LSE phase returns data", lse_phase is not None)
        check(f"LSE phase: {lse_phase.get('phase', 'UNKNOWN')}", True)
        check(f"LSE entries allowed: {lse_phase.get('allow_new_entries')}", True)

        # Check that UK_ISA mode uses LSE phases (not US ET)
        us_phase = sm.get_current_phase()
        if lse_phase.get("phase", "").startswith("LSE_"):
            check("LSE phase has LSE_ prefix", True)
        else:
            warn("LSE phase missing LSE_ prefix", lse_phase.get("phase"))
    except Exception as e:
        check("Session phase check", False, str(e))

    # 4. DATABASE CHECKS
    print("\n[4] DATABASE")
    try:
        db_path = cfg.get_db_path()
        if not os.path.isabs(db_path):
            db_path = str(Path(__file__).parent.parent / db_path)
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        check(f"Database accessible ({len(tables)} tables)", len(tables) > 10, f"Only {len(tables)} tables")

        required_tables = ["trades", "signals", "regime_history", "lse_registry"]
        for t in required_tables:
            check(f"Table '{t}' exists", t in tables, "MISSING")
        conn.close()
    except Exception as e:
        check("Database check", False, str(e))

    # 5. SPREAD/COST MODEL CHECKS
    print("\n[5] COST MODEL")
    try:
        from execution.cost_model import SPREAD_BPS, get_spread_bps, round_trip_cost_bps
        check(f"Spread table has {len(SPREAD_BPS)} tickers", len(SPREAD_BPS) >= 20)

        # Check key tickers exist
        for t in ["QQQ3.L", "3OIL.L", "PHAU.L", "TSL3.L"]:
            check(f"Spread for {t}", t in SPREAD_BPS, "MISSING from spread table")
    except Exception as e:
        check("Cost model check", False, str(e))

    # 6. KELLY SIZER CHECKS
    print("\n[6] KELLY SIZER")
    try:
        from bots.kelly_sizer import get_leverage, _LEVERAGE_MAP
        check(f"Leverage map has {len(_LEVERAGE_MAP)} tickers", len(_LEVERAGE_MAP) >= 20)

        for t in ["QQQ3.L", "3OIL.L", "PHAU.L"]:
            lev = get_leverage(t)
            check(f"Leverage for {t} = {lev}", lev > 0)
    except Exception as e:
        check("Kelly sizer check", False, str(e))

    # 7. API ENDPOINT CHECK
    print("\n[7] API ENDPOINTS")
    try:
        import urllib.request
        endpoints = [
            ("http://localhost:8000/api/state", "state"),
            ("http://localhost:8000/api/health", "health"),
        ]
        for url, name in endpoints:
            try:
                resp = urllib.request.urlopen(url, timeout=5)
                check(f"API /{name} responds ({resp.status})", resp.status == 200)
            except Exception as e:
                warn(f"API /{name} unreachable", str(e))
    except Exception as e:
        warn("API check skipped", str(e))

    # 8. INTEGRATION: Signal pipeline check
    print("\n[8] SIGNAL PIPELINE INTEGRATION")
    try:
        # Check that main.py uses LSE phases for UK_ISA
        main_py = Path(__file__).parent.parent / "main.py"
        content = main_py.read_text()
        check("main.py uses get_current_lse_phase for UK_ISA",
              "get_current_lse_phase" in content,
              "CRITICAL: Still using US ET phases!")

        # Check plays artifact exists for today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        plays_path = Path(__file__).parent.parent / "artifacts" / today / "lse" / "plays.json"
        if plays_path.exists():
            with open(plays_path) as f:
                plays = json.load(f)
            check(f"Today's plays artifact exists ({plays.get('total_plays', 0)} plays)", True)
        else:
            warn("No plays artifact for today yet", "Normal if just deployed")
    except Exception as e:
        check("Signal pipeline check", False, str(e))

    # SUMMARY
    print("\n" + "=" * 60)
    total = CHECKS_PASSED + CHECKS_FAILED
    if CHECKS_FAILED == 0:
        print(f"ALL {total} CHECKS PASSED ({WARNINGS} warnings)")
        return 0
    else:
        print(f"FAILED: {CHECKS_FAILED}/{total} checks failed ({WARNINGS} warnings)")
        return 1

if __name__ == "__main__":
    sys.exit(main())
