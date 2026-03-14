#!/bin/bash
# Pre-Live Deployment Checklist

echo "==============================================="
echo "PRE-LIVE DEPLOYMENT CHECKLIST"
echo "==============================================="
echo ""

CHECKS_PASSED=0
CHECKS_FAILED=0

check_module() {
    if [ -f "$1" ]; then
        echo "✅ $2"
        ((CHECKS_PASSED++))
        return 0
    else
        echo "❌ $2 (missing: $1)"
        ((CHECKS_FAILED++))
        return 1
    fi
}

echo "Core Modules:"
check_module "src/core/early_detection_engine.py" "Early detection engine"
check_module "src/core/perfect_entry_filter.py" "Perfect entry filter"
check_module "src/core/adaptive_ladder.py" "Adaptive ladder"
check_module "src/core/stop_ratchet_memory.py" "Stop ratchet memory"

echo ""
echo "New Modules:"
check_module "src/alerting/telegram_alerter.py" "Telegram alerter"
check_module "src/universe/tiered_universe_scanner.py" "Universe scanner"
check_module "core/live_safety_enforcer.py" "Safety enforcer"
check_module "scripts/gradual_rollout.py" "Gradual rollout"

echo ""
echo "==============================================="
echo "SUMMARY: $CHECKS_PASSED passed, $CHECKS_FAILED failed"
if [ $CHECKS_FAILED -eq 0 ]; then
    echo "✅ ALL CHECKS PASS - APPROVED FOR DEPLOYMENT"
else
    echo "❌ BLOCKERS FOUND - FIX BEFORE DEPLOYING"
fi
echo "==============================================="
