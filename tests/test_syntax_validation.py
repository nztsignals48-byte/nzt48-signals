"""
Python syntax validation for all critical modules
11/10 quality: ensures all code compiles cleanly
"""

import py_compile
import sys
import os

critical_python_files = [
    'main.py',
    'core/master_orchestrator.py',
    'strategies/daily_target.py',
    'strategies/mean_reversion.py',
    'qualification/risk_sizer.py',
    'core/chandelier_exit.py',
    'core/cross_asset_macro.py',
    'core/ml_meta_model.py',
    'core/__init__.py',
    'infrastructure/dual_event_loop.py',
]

print("\n" + "="*70)
print("PYTHON SYNTAX VALIDATION")
print("="*70 + "\n")

passed = 0
failed = 0
errors = []

for filepath in critical_python_files:
    full_path = f'/Users/rr/nzt48-signals/{filepath}'
    
    if not os.path.exists(full_path):
        print(f"⚠️  {filepath:45s} (not found)")
        continue
    
    try:
        py_compile.compile(full_path, doraise=True)
        print(f"✅ {filepath:45s}")
        passed += 1
    except py_compile.PyCompileError as e:
        print(f"❌ {filepath:45s}")
        errors.append((filepath, str(e)))
        failed += 1

print("\n" + "="*70)
print(f"Syntax Check Results: {passed} passed, {failed} failed")
print("="*70)

if errors:
    print("\nError Details:")
    for filepath, error in errors:
        print(f"\n{filepath}:")
        print(f"  {error[:100]}")
    sys.exit(1)
else:
    print("\n✅ All Python files have valid syntax")
    sys.exit(0)
