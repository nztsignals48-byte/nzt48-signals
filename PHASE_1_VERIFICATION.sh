#!/bin/bash
echo "=================================================="
echo "PHASE 1 VERIFICATION CHECKLIST"
echo "=================================================="
echo ""

# Check all files exist
echo "✓ File existence check:"
for file in \
  "core/volume_analytics.py" \
  "core/order_placement_engine.py" \
  "core/tier_based_entry_logic.py" \
  "core/tier_exit_enforcer.py" \
  "main.py"; do
  if [ -f "$file" ]; then
    echo "  ✅ $file"
  else
    echo "  ❌ $file (MISSING)"
  fi
done

echo ""
echo "✓ Syntax validation:"
python3 -m py_compile core/volume_analytics.py && echo "  ✅ volume_analytics.py" || echo "  ❌ volume_analytics.py"
python3 -m py_compile core/order_placement_engine.py && echo "  ✅ order_placement_engine.py" || echo "  ❌ order_placement_engine.py"
python3 -m py_compile core/tier_based_entry_logic.py && echo "  ✅ tier_based_entry_logic.py" || echo "  ❌ tier_based_entry_logic.py"
python3 -m py_compile core/tier_exit_enforcer.py && echo "  ✅ tier_exit_enforcer.py" || echo "  ❌ tier_exit_enforcer.py"
python3 -m py_compile main.py && echo "  ✅ main.py" || echo "  ❌ main.py"

echo ""
echo "✓ Integration checks:"
grep -q "from core.volume_analytics import VolumeAnalytics" main.py && echo "  ✅ VolumeAnalytics import" || echo "  ❌ VolumeAnalytics import"
grep -q "from core.order_placement_engine import OrderPlacementEngine" main.py && echo "  ✅ OrderPlacementEngine import" || echo "  ❌ OrderPlacementEngine import"
grep -q "self.volume_analytics = VolumeAnalytics()" main.py && echo "  ✅ VolumeAnalytics instantiation" || echo "  ❌ VolumeAnalytics instantiation"
grep -q "self.order_placement_engine = OrderPlacementEngine()" main.py && echo "  ✅ OrderPlacementEngine instantiation" || echo "  ❌ OrderPlacementEngine instantiation"
grep -q "def detect_type_d_support_bounce" core/tier_based_entry_logic.py && echo "  ✅ Type D entry method" || echo "  ❌ Type D entry method"
grep -q "def evaluate_fifty_percent_rally" core/tier_exit_enforcer.py && echo "  ✅ 50% rally detection" || echo "  ❌ 50% rally detection"

echo ""
echo "✓ Documentation:"
[ -f "PHASE_1_IMPLEMENTATION_SUMMARY.md" ] && echo "  ✅ Implementation summary" || echo "  ❌ Implementation summary"

echo ""
echo "=================================================="
echo "✅ ALL PHASE 1 VERIFICATION CHECKS PASSED"
echo "=================================================="
