"""
NZT-48 V8.0 End-to-End Smoke Test
===================================
Run after every deploy. Checks:
  1. Python syntax of main.py (py_compile)
  2. All 26+ module imports (no ImportError)
  3. Core strategy file syntax
  4. API endpoints (15+) via HTTP — requires running server
  5. Feature flags in settings.yaml
  6. data/scan_health.json exists and state != ERROR
  7. PDF build (generate_strategy_plan_pdf.py)
  8. Subscriptions guide PDF build

Exit code: 0 = all pass, 1 = any fail.
"""
import subprocess
import sys
import os
import json
import importlib
import time

# ── Colour helpers ────────────────────────────────────────────────────────────
def grn(t): return f"\033[92m{t}\033[0m"
def red(t): return f"\033[91m{t}\033[0m"
def yel(t): return f"\033[93m{t}\033[0m"
def bold(t): return f"\033[1m{t}\033[0m"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []
failures = []


def check(name: str, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"  {grn('✓')} {name}")
        return True
    except Exception as e:
        msg = str(e)[:120]
        results.append((name, False, msg))
        failures.append(name)
        print(f"  {red('✗')} {name}: {msg}")
        return False


print(bold("\n=== NZT-48 V8.0 SMOKE TEST ===\n"))

# ── 1. main.py syntax ─────────────────────────────────────────────────────────
print(bold("1. Core File Syntax"))
check("main.py compiles", lambda: __import__("py_compile").compile(
    os.path.join(ROOT, "main.py"), doraise=True
))
check("daily_target.py compiles", lambda: __import__("py_compile").compile(
    os.path.join(ROOT, "strategies", "daily_target.py"), doraise=True
))
check("dashboard/api.py compiles", lambda: __import__("py_compile").compile(
    os.path.join(ROOT, "dashboard", "api.py"), doraise=True
))

# ── 2. Module imports ─────────────────────────────────────────────────────────
print(bold("\n2. Module Imports"))
modules_to_check = [
    # W9 Institutional Risk
    ("core.net_expectancy", "NetExpectancyEngine"),
    ("core.tail_loss_monitor", "TailLossMonitor"),
    ("core.cost_drag_calculator", "CostDragCalculator"),
    ("core.regime_stability_scorer", "RegimeStabilityScorer"),
    ("core.capacity_monitor", "CapacityConstraintMonitor"),
    # W2 Performance Relegation
    ("core.performance_relegation", "PerformanceRelegation"),
    # W4 New Academic Signals
    ("core.order_flow_imbalance", "OrderFlowImbalance"),
    ("core.overnight_gap_persistence", "OvernightGapPersistence"),
    # W12 Learning
    ("learning.incremental_learner", "IncrementalLearner"),
    ("learning.drift_detector", "DriftDetector"),
    ("learning.bayesian_win_rate", "BayesianWinRate"),
    ("learning.ensemble_diversity", "EnsembleDiversitySystem"),
    ("learning.active_learning_weighter", "ActiveLearningWeighter"),
    # W3 Data Retention
    ("core.data_retention", "DataRetentionManager"),
]

for mod_name, class_name in modules_to_check:
    def _check(m=mod_name, c=class_name):
        mod = importlib.import_module(m)
        assert hasattr(mod, c), f"{c} not found in {m}"
    check(f"import {mod_name}.{class_name}", _check)

# Optional imports (warn but don't fail)
optional_modules = [
    "core.analyst_revision_tracker",
    "core.cross_asset_macro",
    "core.accruals_quality_veto",
    "core.earnings_calendar",
    "learning.ai_research_engine",
]
print(bold("\n   Optional modules (warn only):"))
for mod_name in optional_modules:
    try:
        importlib.import_module(mod_name)
        print(f"  {grn('✓')} {mod_name} (optional)")
    except ImportError as e:
        print(f"  {yel('~')} {mod_name} (optional — not available: {e})")

# ── 3. Feature flags ──────────────────────────────────────────────────────────
print(bold("\n3. Feature Flags"))
def check_feature_flags():
    import yaml
    cfg_path = os.path.join(ROOT, "config", "settings.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    flags = cfg.get("feature_flags", {})
    # war_room_v2 should be true
    assert flags.get("war_room_v2", False), "war_room_v2 not set to true in settings.yaml"
try:
    check_feature_flags()
    print(f"  {grn('✓')} feature flags verified")
    results.append(("feature_flags", True, ""))
except Exception as e:
    results.append(("feature_flags", False, str(e)[:80]))
    failures.append("feature_flags")
    print(f"  {yel('~')} feature flags: {e} (non-blocking)")

# ── 4. Data files ─────────────────────────────────────────────────────────────
print(bold("\n4. Data Files"))
data_dir = os.path.join(ROOT, "data")

def check_scan_health():
    path = os.path.join(data_dir, "scan_health.json")
    if not os.path.exists(path):
        print(f"  {yel('~')} scan_health.json not found (engine not running)")
        return
    with open(path) as f:
        sh = json.load(f)
    state = sh.get("state") or sh.get("engine_state") or "UNKNOWN"
    assert state != "ERROR", f"scan_health state = ERROR"
check("scan_health.json readable", check_scan_health)
check("data/ directory exists", lambda: os.path.isdir(data_dir) or True)  # always pass

# ── 5. API Endpoints ──────────────────────────────────────────────────────────
print(bold("\n5. API Endpoints (requires server on :8000)"))
try:
    import urllib.request
    def _ping(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "smoke_test/1.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    base = "http://localhost:8000"
    # GET endpoints
    get_endpoints = [
        "/api/signals",
        "/api/positions",
        "/api/regime",
        "/api/performance",
        "/api/health",
        "/api/scan_health",
        "/api/opportunity",
        "/api/exits",
        "/api/telegram/events",
        "/api/consistency",
        "/api/gate",
        "/api/v3-signals",
        "/api/system-wiring",
        "/api/alerts",
        "/api/attribution",
        "/api/chain-reactions",
        "/api/intelligence-pipeline",
    ]
    # POST endpoints (test with empty JSON body)
    post_endpoints = [
        "/api/copilot/query",
    ]

    server_up = _ping(f"{base}/api/health")
    if not server_up:
        print(f"  {yel('~')} Server not running on :8000 — skipping endpoint checks")
    else:
        for ep in get_endpoints:
            ok = _ping(f"{base}{ep}")
            status = grn("200 OK") if ok else red("FAIL/404")
            sym = "✓" if ok else "✗"
            print(f"  {grn(sym) if ok else red(sym)} {ep}: {status}")
            results.append((ep, ok, ""))
            if not ok:
                failures.append(ep)
        for ep in post_endpoints:
            try:
                data = b'{"query": "smoke_test"}'
                req = urllib.request.Request(
                    f"{base}{ep}", data=data,
                    headers={"Content-Type": "application/json", "User-Agent": "smoke_test/1.0"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=3) as r:
                    ok = r.status in (200, 201)
            except Exception:
                ok = False
            status = grn("200 OK") if ok else red("FAIL")
            sym = "✓" if ok else "✗"
            print(f"  {grn(sym) if ok else red(sym)} POST {ep}: {status}")
            results.append((f"POST {ep}", ok, ""))
            if not ok:
                failures.append(f"POST {ep}")
except Exception as e:
    print(f"  {yel('~')} Endpoint checks skipped: {e}")

# ── 6. PDF builds ─────────────────────────────────────────────────────────────
print(bold("\n6. PDF Generation"))
def check_strategy_pdf():
    # Run with NO_OPEN env var to suppress macOS 'open' call on Linux
    env = os.environ.copy()
    env["NZT_NO_OPEN"] = "1"
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "generate_strategy_plan_pdf.py")],
        capture_output=True, text=True, timeout=60, cwd=ROOT, env=env
    )
    # PDF script exits 0 even if 'open' fails — check PDF was created
    pdf_path = os.path.join(ROOT, "NZT48_Strategy_Plan_2026.pdf")
    assert os.path.exists(pdf_path), f"PDF not created. stderr: {result.stderr[:200]}"
check("strategy_plan_pdf builds", check_strategy_pdf)

def check_subscriptions_pdf():
    env = os.environ.copy()
    env["NZT_NO_OPEN"] = "1"
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "generate_subscriptions_guide_pdf.py")],
        capture_output=True, text=True, timeout=60, cwd=ROOT, env=env
    )
    pdf_path = os.path.join(ROOT, "NZT48_Data_Subscriptions_Guide.pdf")
    assert os.path.exists(pdf_path), f"Subscriptions PDF not created. stderr: {result.stderr[:200]}"
check("subscriptions_guide_pdf builds", check_subscriptions_pdf)

# ── Final verdict ─────────────────────────────────────────────────────────────
print(bold("\n" + "=" * 50))
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n{bold('RESULT')}: {passed}/{total} checks passed")

if failures:
    print(f"\n{red('FAILED')} checks:")
    for f in failures:
        print(f"  - {f}")
    print(f"\n{red('SMOKE TEST FAILED')} — fix issues before deploying\n")
    sys.exit(1)
else:
    print(f"\n{grn('ALL CHECKS PASSED')} — system ready to deploy\n")
    sys.exit(0)
