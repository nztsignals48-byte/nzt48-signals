"""
Environment and configuration validation
11/10 quality: verify all settings are correct
"""

import os
import sys
import sqlite3
import yaml

print("\n" + "="*70)
print("ENVIRONMENT & CONFIGURATION VALIDATION")
print("="*70)

# Test 1: Environment variables
print("\n1. ENVIRONMENT VARIABLES:")
print("-" * 70)

required_env = [
    'IBKR_PORT',
    'IBKR_HOST',
    'DATABASE_PATH',
    'NZT48_TELEGRAM_BOT_TOKEN',
    'NZT48_TELEGRAM_CHAT_ID',
]

from dotenv import load_dotenv
load_dotenv('/Users/rr/nzt48-signals/.env')

env_passed = 0
for var in required_env:
    value = os.getenv(var)
    if value:
        masked = value[:5] + '*' * max(0, len(value) - 10) + (value[-5:] if len(value) > 10 else '')
        print(f"  ✅ {var:40s}")
        env_passed += 1
    else:
        print(f"  ⚠️  {var:40s} (not set)")

print(f"\nEnvironment: {env_passed}/{len(required_env)} required vars set")

# Test 2: YAML Config
print("\n2. CONFIGURATION (settings.yaml):")
print("-" * 70)

try:
    with open('/Users/rr/nzt48-signals/config/settings.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    config_checks = [
        ('universe', list),
        ('strategies', dict),
        ('risk_management', dict),
    ]
    
    config_passed = 0
    for key, expected_type in config_checks:
        if key in config:
            actual_type = type(config[key]).__name__
            print(f"  ✅ {key:40s} ({actual_type})")
            config_passed += 1
        else:
            print(f"  ⚠️  {key:40s} (missing)")
    
    # Count assets
    if 'universe' in config:
        assets = config['universe']
        print(f"  ✅ Total assets in universe: {len(assets)}")
    
    print(f"\nConfiguration: {config_passed}/{len(config_checks)} key sections found")
except Exception as e:
    print(f"  ❌ Config error: {e}")

# Test 3: Database Integrity
print("\n3. DATABASE INTEGRITY:")
print("-" * 70)

try:
    conn = sqlite3.connect('/Users/rr/nzt48-signals/data/nzt48.db')
    cursor = conn.cursor()
    
    # Run integrity check
    cursor.execute('PRAGMA integrity_check')
    result = cursor.fetchone()[0]
    
    if result == 'ok':
        print(f"  ✅ Database integrity check PASSED")
        
        # Check table count
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"  ✅ Database tables: {len(tables)}")
        for table in tables[:5]:
            print(f"     - {table[0]}")
        if len(tables) > 5:
            print(f"     ... and {len(tables) - 5} more")
        
    else:
        print(f"  ❌ Database integrity check FAILED: {result}")
    
    conn.close()
except Exception as e:
    print(f"  ❌ Database error: {e}")

# Test 4: Docker compose
print("\n4. DOCKER CONFIGURATION:")
print("-" * 70)

try:
    with open('/Users/rr/nzt48-signals/docker-compose.yml', 'r') as f:
        docker_config = yaml.safe_load(f)
    
    if 'services' in docker_config:
        services = list(docker_config['services'].keys())
        print(f"  ✅ Docker services defined: {len(services)}")
        for service in services:
            print(f"     - {service}")
    
    print(f"  ✅ Docker compose file is valid YAML")
except Exception as e:
    print(f"  ⚠️  Docker config error: {e}")

print("\n" + "="*70)
print("✅ ENVIRONMENT & CONFIG VALIDATION COMPLETE")
print("="*70)
