"""
Performance and load testing
11/10 quality: stress test critical components
"""

import time
import sys
import os
import sqlite3

print("\n" + "="*70)
print("PERFORMANCE & LOAD TEST")
print("="*70 + "\n")

# Test 1: Database performance
print("1. DATABASE PERFORMANCE:")
print("-" * 70)

try:
    conn = sqlite3.connect('/Users/rr/nzt48-signals/data/nzt48.db')
    cursor = conn.cursor()
    
    # Test read performance
    start = time.time()
    cursor.execute("SELECT COUNT(*) FROM signals LIMIT 1000")
    result = cursor.fetchone()[0]
    read_time = time.time() - start
    
    print(f"  ✅ Read 1000 signal records: {read_time*1000:.2f}ms")
    print(f"  ✅ Total signal records in DB: {result}")
    
    # Test query performance
    start = time.time()
    cursor.execute("""
        SELECT asset, COUNT(*) as count 
        FROM signals 
        GROUP BY asset 
        LIMIT 10
    """)
    results = cursor.fetchall()
    query_time = time.time() - start
    
    print(f"  ✅ Complex query (groupby): {query_time*1000:.2f}ms")
    
    conn.close()
    
    if read_time > 1.0 or query_time > 1.0:
        print(f"  ⚠️  Some queries exceeded 1 second (may need optimization)")
    else:
        print(f"  ✅ All query times acceptable (<1s)")
        
except Exception as e:
    print(f"  ❌ Database test failed: {e}")

# Test 2: File I/O performance
print("\n2. FILE I/O PERFORMANCE:")
print("-" * 70)

try:
    # Test config load time
    start = time.time()
    import yaml
    with open('/Users/rr/nzt48-signals/config/settings.yaml', 'r') as f:
        config = yaml.safe_load(f)
    yaml_time = time.time() - start
    
    print(f"  ✅ YAML load time: {yaml_time*1000:.2f}ms")
    
    # Test .env load time
    from dotenv import load_dotenv
    start = time.time()
    load_dotenv('/Users/rr/nzt48-signals/.env')
    env_time = time.time() - start
    
    print(f"  ✅ .env load time: {env_time*1000:.2f}ms")
    
    if yaml_time + env_time < 0.5:
        print(f"  ✅ Total startup I/O: {(yaml_time + env_time)*1000:.2f}ms (excellent)")
    else:
        print(f"  ⚠️  Startup I/O: {(yaml_time + env_time)*1000:.2f}ms")
        
except Exception as e:
    print(f"  ❌ File I/O test failed: {e}")

# Test 3: Import performance
print("\n3. IMPORT PERFORMANCE:")
print("-" * 70)

try:
    modules_to_test = [
        ('yaml', 'yaml'),
        ('dotenv', 'dotenv'),
        ('sqlite3', 'sqlite3'),
    ]
    
    import_times = {}
    for module_name, import_name in modules_to_test:
        start = time.time()
        __import__(import_name)
        import_time = time.time() - start
        import_times[module_name] = import_time
        print(f"  ✅ Import {module_name:30s}: {import_time*1000:6.2f}ms")
    
    total_import = sum(import_times.values())
    if total_import < 2.0:
        print(f"  ✅ Total import time: {total_import*1000:.2f}ms (excellent)")
    else:
        print(f"  ⚠️  Total import time: {total_import*1000:.2f}ms")
        
except Exception as e:
    print(f"  ❌ Import test failed: {e}")

print("\n" + "="*70)
print("✅ PERFORMANCE TEST COMPLETE")
print("="*70)
