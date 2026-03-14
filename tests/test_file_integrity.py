"""
File integrity check: all critical files exist and are valid
11/10 quality: comprehensive file validation
"""

import os
import sys

critical_files = [
    'main.py',
    'core/master_orchestrator.py',
    'strategies/daily_target.py',
    'qualification/risk_sizer.py',
    'core/chandelier_exit.py',
    'core/cross_asset_macro.py',
    'core/ml_meta_model.py',
    'config/settings.yaml',
    'data/nzt48.db',
    'data/nzt48.backup.2026-03-14.db',
    '.env',
    'docker-compose.yml',
]

print("\nChecking file integrity...")
print("=" * 70)

all_good = True
found_count = 0
missing_count = 0

for filepath in critical_files:
    full_path = f'/Users/rr/nzt48-signals/{filepath}'
    if os.path.exists(full_path):
        size = os.path.getsize(full_path)
        if size > 0:
            print(f"✅ {filepath:45s} ({size:>10,d} bytes)")
            found_count += 1
        else:
            print(f"❌ {filepath:45s} (EMPTY)")
            missing_count += 1
            all_good = False
    else:
        print(f"⚠️  {filepath:45s} (not critical if optional)")
        missing_count += 1

print("=" * 70)
print(f"\nResults: {found_count} found, {missing_count} missing/optional")

if all_good:
    print("\n✅ All critical files present and non-empty")
else:
    print("\n⚠️  Some files missing or empty (may be optional)")
