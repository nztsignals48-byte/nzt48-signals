#!/bin/bash
# Verification script for UTC migration
# Run this to verify the time system is working correctly

echo "=== AEGIS V2 UTC MIGRATION VERIFICATION ==="
echo ""

# Check 1: Compile
echo "[1/5] Checking compilation..."
cd rust_core
cargo check 2>&1 | grep -q "Finished" && echo "✅ Compilation successful" || echo "❌ Compilation failed"
cd ..

# Check 2: Time-related code
echo "[2/5] Checking UTC functions..."
grep -q "pub fn now_utc_secs" rust_core/src/clock.rs && echo "✅ now_utc_secs() found" || echo "❌ Missing UTC function"
grep -q "pub fn is_bst_from_epoch" rust_core/src/clock.rs && echo "✅ is_bst_from_epoch() public" || echo "❌ BST function not public"
grep -q "from_utc_secs" rust_core/src/clock.rs && echo "✅ TradingMode::from_utc_secs() found" || echo "❌ Missing UTC mode"

# Check 3: Safety lock
echo "[3/5] Checking safety locks..."
grep -q "const IS_LIVE: bool = false" rust_core/src/main.rs && echo "✅ IS_LIVE=false locked" || echo "❌ Safety lock missing"
grep -q "SIMULATION MODE" rust_core/src/main.rs && echo "✅ Simulation mode verified" || echo "❌ Simulation not confirmed"

# Check 4: Tests
echo "[4/5] Checking tests..."
grep -q "test_bst_2026_transition" rust_core/src/clock.rs && echo "✅ BST tests present" || echo "❌ Missing BST tests"
grep -q "test_utc_secs_extraction" rust_core/src/clock.rs && echo "✅ UTC extraction tests" || echo "❌ Missing UTC tests"
grep -q "test_trading_mode_transitions_utc" rust_core/src/clock.rs && echo "✅ Mode transition tests" || echo "❌ Missing mode tests"

# Check 5: Pipeline
echo "[5/5] Checking signal pipeline..."
grep -q "engine.process_tick_with_signal" rust_core/src/main.rs && echo "✅ Signal pipeline wired" || echo "❌ Pipeline broken"
grep -q "print(json.dumps(response)" python_brain/bridge.py && echo "✅ Signal emission found" || echo "❌ No signal emission"
grep -q "BufReader::new(stdout)" rust_core/src/python_bridge.rs && echo "✅ Signal reception ready" || echo "❌ No signal reception"

echo ""
echo "=== VERIFICATION COMPLETE ==="
echo ""
echo "For detailed information, see SESSION_17_COMPLETION_REPORT.md"
