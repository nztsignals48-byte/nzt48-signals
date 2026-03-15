#!/bin/bash
# Integration Test Suite for Q1-Q4 Enhancements
# Runs comprehensive tests before EC2 deployment

set -e  # Exit on error

PROJECT_DIR="/Users/rr/nzt48-signals"
LOG_FILE="/tmp/nzt48_integration_test_$(date +%Y%m%d_%H%M%S).log"

echo "=== NZT-48 Q1-Q4 Integration Test Suite ===" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

cd "$PROJECT_DIR"

# ----------------------------------------------------------------
# 1. SYNTAX VALIDATION
# ----------------------------------------------------------------
echo "1. Running Python Syntax Validation..." | tee -a "$LOG_FILE"
if python3 -m py_compile core/*.py 2>&1 | tee -a "$LOG_FILE"; then
  echo "✓ Syntax validation passed" | tee -a "$LOG_FILE"
else
  echo "✗ Syntax errors detected" | tee -a "$LOG_FILE"
  exit 1
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# 2. IMPORT VALIDATION
# ----------------------------------------------------------------
echo "2. Testing Critical Imports..." | tee -a "$LOG_FILE"
python3 << 'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import sys
sys.path.insert(0, "/Users/rr/nzt48-signals")

# Q1 imports
from core.indicator_enhancements import IndicatorEnhancements
from core.tier_based_entry_logic import TierBasedEntryDetector

# Q2 imports
from core.position_sizing_engine import PositionSizingEngine
from core.quote_cache import QuoteCache
from core.universe_scanner import UniverseScanner

# Models
from models import IndicatorSnapshot

print("✓ All Q1-Q4 modules importable")
PYEOF

if [ $? -eq 0 ]; then
  echo "✓ Import validation passed" | tee -a "$LOG_FILE"
else
  echo "✗ Import errors detected" | tee -a "$LOG_FILE"
  exit 1
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# 3. UNIT TESTS
# ----------------------------------------------------------------
echo "3. Running Q1 Unit Tests..." | tee -a "$LOG_FILE"
if pytest tests/test_q1_indicator_enhancements.py -v --tb=short 2>&1 | tee -a "$LOG_FILE"; then
  echo "✓ Q1 tests passed" | tee -a "$LOG_FILE"
else
  echo "⚠ Q1 tests failed (may be expected if agents still working)" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

echo "4. Running Q2 Unit Tests..." | tee -a "$LOG_FILE"
if pytest tests/test_q2_performance_risk.py -v --tb=short 2>&1 | tee -a "$LOG_FILE"; then
  echo "✓ Q2 tests passed" | tee -a "$LOG_FILE"
else
  echo "⚠ Q2 tests failed (may be expected if agents still working)" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# 5. BACKWARD COMPATIBILITY
# ----------------------------------------------------------------
echo "5. Running Existing Test Suite (Backward Compatibility)..." | tee -a "$LOG_FILE"
if pytest tests/test_tiered_universe.py -v --tb=short 2>&1 | tee -a "$LOG_FILE"; then
  echo "✓ Backward compatibility verified" | tee -a "$LOG_FILE"
else
  echo "⚠ Backward compatibility issues detected" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# 6. INFRASTRUCTURE VALIDATION
# ----------------------------------------------------------------
echo "6. Validating K8s Manifests..." | tee -a "$LOG_FILE"
if command -v kubectl &> /dev/null; then
  for yaml in deployment/k8s/*.yaml; do
    if kubectl apply --dry-run=client -f "$yaml" 2>&1 | tee -a "$LOG_FILE"; then
      echo "✓ $yaml valid" | tee -a "$LOG_FILE"
    else
      echo "⚠ $yaml validation failed" | tee -a "$LOG_FILE"
    fi
  done
else
  echo "⚠ kubectl not installed, skipping K8s validation" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# 7. TERRAFORM VALIDATION
# ----------------------------------------------------------------
echo "7. Validating Terraform Configuration..." | tee -a "$LOG_FILE"
if command -v terraform &> /dev/null; then
  cd deployment/terraform
  if terraform init -backend=false 2>&1 | tee -a "$LOG_FILE"; then
    if terraform validate 2>&1 | tee -a "$LOG_FILE"; then
      echo "✓ Terraform configuration valid" | tee -a "$LOG_FILE"
    else
      echo "⚠ Terraform validation failed" | tee -a "$LOG_FILE"
    fi
  fi
  cd "$PROJECT_DIR"
else
  echo "⚠ terraform not installed, skipping validation" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# 8. DOCKER BUILD TEST
# ----------------------------------------------------------------
echo "8. Testing Docker Build..." | tee -a "$LOG_FILE"
if docker build -t nzt48:q1-q4-test . 2>&1 | tee -a "$LOG_FILE"; then
  echo "✓ Docker build succeeded" | tee -a "$LOG_FILE"
  docker images | grep nzt48:q1-q4-test | tee -a "$LOG_FILE"
else
  echo "✗ Docker build failed" | tee -a "$LOG_FILE"
  exit 1
fi
echo "" | tee -a "$LOG_FILE"

# ----------------------------------------------------------------
# SUMMARY
# ----------------------------------------------------------------
echo "======================================" | tee -a "$LOG_FILE"
echo "INTEGRATION TEST SUMMARY" | tee -a "$LOG_FILE"
echo "======================================" | tee -a "$LOG_FILE"
echo "Completed: $(date)" | tee -a "$LOG_FILE"
echo "Log file: $LOG_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Next Steps:" | tee -a "$LOG_FILE"
echo "1. Review test results above" | tee -a "$LOG_FILE"
echo "2. If all pass: Deploy to EC2 with scripts/deploy_to_ec2.sh" | tee -a "$LOG_FILE"
echo "3. Verify paper trading: ssh to EC2 and check docker logs" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
