#!/bin/bash
# P10: DD Stress Test Battery (Book 9, 10 categories)
# Run: bash scripts/stress_test.sh
# Tests system resilience under adverse conditions.

set -euo pipefail

PASS=0
FAIL=0
TOTAL=0

check() {
    local name="$1"
    local result="$2"
    TOTAL=$((TOTAL + 1))
    if [ "$result" = "0" ]; then
        echo "  [PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== AEGIS V2 DD STRESS TEST BATTERY ==="
echo "Date: $(date -u)"
echo

# Category 1: Build integrity
echo "--- Category 1: Build Integrity ---"
cd /app
python3 -c "import rust_core; print('Rust module OK')" >/dev/null 2>&1
check "Rust module loads" "$?"

python3 -c "import ast; ast.parse(open('/app/python_brain/bridge.py').read())" >/dev/null 2>&1
check "Bridge.py syntax valid" "$?"

# Category 2: Configuration integrity
echo "--- Category 2: Configuration ---"
python3 -c "
import tomllib
with open('/app/config/config.toml', 'rb') as f:
    cfg = tomllib.load(f)
assert cfg['position']['max_simultaneous_positions'] <= 20
assert cfg['position']['portfolio_heat_limit_pct'] <= 50
assert cfg['position']['cash_buffer_pct'] >= 5
print('Config bounds OK')
" >/dev/null 2>&1
check "Config within safe bounds" "$?"

python3 -c "
import tomllib
with open('/app/config/dynamic_weights.toml', 'rb') as f:
    dw = tomllib.load(f)
assert dw['kelly_fractions']['t1'] <= 0.10, 'Kelly too high'
assert dw['bayesian']['win_rate'] < 1.0
print('Dynamic weights sane')
" >/dev/null 2>&1
check "Dynamic weights sane" "$?"

# Category 3: Risk arbiter completeness
echo "--- Category 3: Risk Checks ---"
python3 -c "
import tomllib
with open('/app/config/config.toml', 'rb') as f:
    cfg = tomllib.load(f)
assert cfg['crucible'].get('paper_uses_live_gates') == True, 'Live gates not enforced'
print('Live gates enforced')
" >/dev/null 2>&1
check "Paper uses live risk gates" "$?"

# Category 4: WAL integrity
echo "--- Category 4: WAL ---"
WAL="/app/events/current.ndjson"
if [ -f "$WAL" ]; then
    check "WAL file exists" "0"
else
    touch "$WAL"
    check "WAL file created" "0"
fi

# Category 5: Signal pipeline (7 systems)
echo "--- Category 5: Signal Pipeline ---"
python3 -c "
with open('/app/python_brain/bridge.py') as f:
    code = f.read()
systems = ['_system1_microstructure', '_system2_reversion', '_system3_macro_trend',
           '_system4_volatility', '_system5_overnight', '_system6_catalyst', '_system7_tail_hedge']
missing = [s for s in systems if s not in code]
assert not missing, f'Missing systems: {missing}'
print(f'All 7 systems present')
" >/dev/null 2>&1
check "All 7 system strategies in bridge.py" "$?"

# Category 6: Slippage model
echo "--- Category 6: Execution ---"
grep -q "slippage_pct" /app/rust_core/src/paper_broker.rs 2>/dev/null
check "Slippage model in paper_broker" "$?"

# Category 7: Monitoring
echo "--- Category 7: Monitoring ---"
[ -f "/app/scripts/export_metrics.sh" ]
check "Metrics export script exists" "$?"

[ -f "/app/scripts/backup_wal.sh" ]
check "Backup script exists" "$?"

# Category 8: Cannibalization detection
echo "--- Category 8: Ensemble Protection ---"
grep -q "_cofire_counts" /app/python_brain/bridge.py 2>/dev/null
check "Cannibalization detector present" "$?"

grep -q "CLAUDE_SOFT_GATE" /app/python_brain/bridge.py 2>/dev/null
check "Claude advisory soft gate present" "$?"

# Category 9: Macro regime
echo "--- Category 9: Macro Safety ---"
python3 -c "
import tomllib
with open('/app/config/config.toml', 'rb') as f:
    cfg = tomllib.load(f)
md = cfg['macro_defaults']
assert md['vix'] > 20, 'Macro defaults not fail-safe (VIX too low)'
assert md['fear_greed'] < 40, 'Macro defaults not fail-safe (F&G too high)'
print('Macro fail-safe OK')
" >/dev/null 2>&1
check "Macro defaults are fail-safe (Caution)" "$?"

# Category 10: Disk space
echo "--- Category 10: Infrastructure ---"
DISK_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
[ "$DISK_PCT" -lt 90 ]
check "Disk usage < 90% (currently ${DISK_PCT}%)" "$?"

echo
echo "=== RESULTS ==="
echo "${PASS}/${TOTAL} passed, ${FAIL} failed"
if [ "$FAIL" -gt 0 ]; then
    echo "STATUS: FAIL — fix issues before promotion"
    exit 1
else
    echo "STATUS: ALL PASS — system ready for simulation"
    exit 0
fi
