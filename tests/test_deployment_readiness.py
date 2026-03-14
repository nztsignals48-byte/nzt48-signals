"""
Deployment readiness check
11/10 quality: verify system is production-ready
"""

import os
import sys
import subprocess

print("\n" + "="*70)
print("DEPLOYMENT READINESS CHECK")
print("="*70 + "\n")

checks_passed = 0
checks_total = 0

# Test 1: Git status
print("1. GIT STATUS:")
print("-" * 70)

checks_total += 1
try:
    result = subprocess.run(
        ['git', '-C', '/Users/rr/nzt48-signals', 'status', '--porcelain'],
        capture_output=True,
        text=True,
        timeout=5
    )
    
    if result.returncode == 0:
        dirty_files = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
        if dirty_files == 0:
            print(f"  ✅ Git working tree clean")
            checks_passed += 1
        else:
            print(f"  ⚠️  {dirty_files} uncommitted changes")
    else:
        print(f"  ⚠️  Git not available or repo error")
except Exception as e:
    print(f"  ⚠️  Git check skipped: {e}")

# Test 2: Docker available
print("\n2. DOCKER CONFIGURATION:")
print("-" * 70)

checks_total += 1
try:
    result = subprocess.run(
        ['docker', '--version'],
        capture_output=True,
        text=True,
        timeout=5
    )
    
    if result.returncode == 0:
        print(f"  ✅ Docker available: {result.stdout.strip()}")
        checks_passed += 1
    else:
        print(f"  ❌ Docker not available")
except Exception as e:
    print(f"  ⚠️  Docker check failed: {e}")

# Test 3: Docker compose file valid
print("\n3. DOCKER COMPOSE:")
print("-" * 70)

checks_total += 1
try:
    result = subprocess.run(
        ['docker', 'compose', '-f', '/Users/rr/nzt48-signals/docker-compose.yml', 'config'],
        capture_output=True,
        text=True,
        timeout=10,
        cwd='/Users/rr/nzt48-signals'
    )
    
    if result.returncode == 0:
        print(f"  ✅ Docker compose file is valid")
        checks_passed += 1
    else:
        print(f"  ⚠️  Docker compose validation failed")
        print(f"     Error: {result.stderr[:100]}")
except Exception as e:
    print(f"  ⚠️  Docker compose check skipped: {e}")

# Test 4: Required binaries
print("\n4. REQUIRED BINARIES:")
print("-" * 70)

checks_total += 1
required_binaries = ['python3', 'sqlite3']
all_present = True

for binary in required_binaries:
    result = subprocess.run(
        ['which', binary],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print(f"  ✅ {binary:20s} {result.stdout.strip()}")
    else:
        print(f"  ❌ {binary:20s} NOT FOUND")
        all_present = False

if all_present:
    checks_passed += 1

# Test 5: Database backup exists
print("\n5. BACKUP STATUS:")
print("-" * 70)

checks_total += 1
backup_files = [
    '/Users/rr/nzt48-signals/data/nzt48.backup.2026-03-14.db',
]

backup_exists = True
for backup_file in backup_files:
    if os.path.exists(backup_file):
        size = os.path.getsize(backup_file)
        print(f"  ✅ Backup exists: {backup_file} ({size:,} bytes)")
    else:
        print(f"  ⚠️  Backup missing: {backup_file}")
        backup_exists = False

if backup_exists:
    checks_passed += 1

# Test 6: Critical configuration files
print("\n6. CONFIGURATION FILES:")
print("-" * 70)

checks_total += 1
config_files = [
    ('.env', 'Environment'),
    ('config/settings.yaml', 'Settings'),
    ('docker-compose.yml', 'Docker'),
]

all_configs_present = True
for filename, description in config_files:
    filepath = f'/Users/rr/nzt48-signals/{filename}'
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        size = os.path.getsize(filepath)
        print(f"  ✅ {description:20s} ({size:,} bytes)")
    else:
        print(f"  ❌ {description:20s} MISSING")
        all_configs_present = False

if all_configs_present:
    checks_passed += 1

# Test 7: Logs and output directories
print("\n7. OUTPUT DIRECTORIES:")
print("-" * 70)

checks_total += 1
output_dirs = [
    'logs',
    'data',
    'reports',
]

all_dirs_exist = True
for dirname in output_dirs:
    dirpath = f'/Users/rr/nzt48-signals/{dirname}'
    if os.path.exists(dirpath) and os.path.isdir(dirpath):
        count = len(os.listdir(dirpath))
        print(f"  ✅ {dirname:20s} (exists, {count} items)")
    else:
        print(f"  ⚠️  {dirname:20s} (may need to be created)")

# Test 8: Documentation
print("\n8. DOCUMENTATION:")
print("-" * 70)

checks_total += 1
doc_files = [
    'README.md',
    'CONTRIBUTING.md',
]

docs_found = 0
for docfile in doc_files:
    filepath = f'/Users/rr/nzt48-signals/{docfile}'
    if os.path.exists(filepath):
        size = os.path.getsize(filepath)
        print(f"  ✅ {docfile:30s} ({size:,} bytes)")
        docs_found += 1
    else:
        print(f"  ⚠️  {docfile:30s} (missing)")

if docs_found > 0:
    checks_passed += 1

# Summary
print("\n" + "="*70)
print("DEPLOYMENT READINESS SUMMARY")
print("="*70)

percentage = int((checks_passed / checks_total) * 100) if checks_total > 0 else 0
print(f"\nChecks Passed: {checks_passed}/{checks_total} ({percentage}%)")

if percentage >= 85:
    print("\n✅ SYSTEM IS READY FOR DEPLOYMENT")
elif percentage >= 70:
    print("\n⚠️  SYSTEM IS MOSTLY READY (some optional items missing)")
else:
    print("\n❌ SYSTEM NEEDS FIXES BEFORE DEPLOYMENT")

print("="*70)
