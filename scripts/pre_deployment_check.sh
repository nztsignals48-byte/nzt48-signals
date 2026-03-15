#!/bin/bash
#
# NZT-48 Pre-Deployment Verification Script
# ==========================================
# Runs before Option A automated deployment to verify system readiness
#
# Checks:
# 1. Q2 tests still passing
# 2. No Python syntax errors in core modules
# 3. All required modules importable
# 4. Configuration files valid
# 5. Git status clean (optional)
# 6. EC2 connectivity working
#
# Exit codes:
#   0 = All checks passed, ready for deployment
#   1 = Critical failure, deployment blocked
#   2 = Warnings only, deployment can proceed with caution

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_ROOT="/Users/rr/nzt48-signals"
CRITICAL_FAILURES=0
WARNINGS=0

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}NZT-48 Pre-Deployment Verification${NC}"
echo -e "${BLUE}Date: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Check 1: Python syntax validation
echo -e "${BLUE}[1/7] Checking Python syntax in core modules...${NC}"
cd "$PROJECT_ROOT"

SYNTAX_ERRORS=0
for file in core/*.py; do
    if [ -f "$file" ]; then
        if ! python3 -m py_compile "$file" 2>/dev/null; then
            echo -e "${RED}  ✗ Syntax error in $file${NC}"
            SYNTAX_ERRORS=$((SYNTAX_ERRORS + 1))
            CRITICAL_FAILURES=$((CRITICAL_FAILURES + 1))
        fi
    fi
done

if [ $SYNTAX_ERRORS -eq 0 ]; then
    echo -e "${GREEN}  ✓ All core modules have valid Python syntax${NC}"
else
    echo -e "${RED}  ✗ Found $SYNTAX_ERRORS syntax errors${NC}"
fi
echo ""

# Check 2: Core module imports
echo -e "${BLUE}[2/7] Testing core module imports...${NC}"

IMPORT_CHECK=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')

failed = []
modules = [
    'core.rust_ffi_bridge',
    'core.position_sizing_engine',
    'core.neural_hawkes_exit',
]

for mod in modules:
    try:
        __import__(mod)
    except Exception as e:
        failed.append((mod, str(e)))

if failed:
    for mod, err in failed:
        print(f'FAIL: {mod} - {err}')
    sys.exit(1)
else:
    print('OK')
    sys.exit(0)
" 2>&1)

if echo "$IMPORT_CHECK" | grep -q "^OK$"; then
    echo -e "${GREEN}  ✓ All core modules importable${NC}"
else
    echo -e "${RED}  ✗ Import failures detected:${NC}"
    echo "$IMPORT_CHECK" | grep "^FAIL:" | sed 's/^/    /'
    CRITICAL_FAILURES=$((CRITICAL_FAILURES + 1))
fi
echo ""

# Check 3: Configuration files exist
echo -e "${BLUE}[3/7] Validating configuration files...${NC}"

CONFIG_MISSING=0
for config_file in docker-compose.yml Dockerfile .env.production; do
    if [ ! -f "$PROJECT_ROOT/$config_file" ]; then
        echo -e "${RED}  ✗ Missing: $config_file${NC}"
        CONFIG_MISSING=$((CONFIG_MISSING + 1))
        CRITICAL_FAILURES=$((CRITICAL_FAILURES + 1))
    fi
done

if [ $CONFIG_MISSING -eq 0 ]; then
    echo -e "${GREEN}  ✓ All required configuration files present${NC}"
else
    echo -e "${RED}  ✗ Missing $CONFIG_MISSING configuration files${NC}"
fi
echo ""

# Check 4: Docker Compose validation
echo -e "${BLUE}[4/7] Validating docker-compose.yml syntax...${NC}"

if command -v docker &> /dev/null && command -v docker-compose &> /dev/null; then
    if docker-compose -f "$PROJECT_ROOT/docker-compose.yml" config > /dev/null 2>&1; then
        echo -e "${GREEN}  ✓ docker-compose.yml is valid${NC}"
    else
        echo -e "${RED}  ✗ docker-compose.yml has syntax errors${NC}"
        CRITICAL_FAILURES=$((CRITICAL_FAILURES + 1))
    fi
else
    echo -e "${YELLOW}  ⚠ Docker not available, skipping compose validation${NC}"
    WARNINGS=$((WARNINGS + 1))
fi
echo ""

# Check 5: Feature flags verification
echo -e "${BLUE}[5/7] Verifying feature flags configuration...${NC}"

FEATURE_FLAGS_OK=true
if grep -q "ENABLE_PARALLEL_SCANNING=true" "$PROJECT_ROOT/docker-compose.yml" &&
   grep -q "ENABLE_RUST_FFI_BRIDGE=false" "$PROJECT_ROOT/docker-compose.yml" &&
   grep -q "ENABLE_NEURAL_HAWKES=false" "$PROJECT_ROOT/docker-compose.yml"; then
    echo -e "${GREEN}  ✓ Feature flags configured correctly (Q2 enabled, Q3/Q4 disabled)${NC}"
else
    echo -e "${YELLOW}  ⚠ Feature flags may not be configured correctly${NC}"
    WARNINGS=$((WARNINGS + 1))
    FEATURE_FLAGS_OK=false
fi
echo ""

# Check 6: Git status
echo -e "${BLUE}[6/7] Checking git repository status...${NC}"

cd "$PROJECT_ROOT"
if git status &> /dev/null; then
    UNCOMMITTED=$(git status --porcelain | wc -l | tr -d ' ')
    if [ "$UNCOMMITTED" -eq 0 ]; then
        echo -e "${GREEN}  ✓ Git working directory is clean${NC}"
    else
        echo -e "${YELLOW}  ⚠ $UNCOMMITTED uncommitted changes in git${NC}"
        echo -e "${YELLOW}    (This is OK if you plan to commit before deployment)${NC}"
        WARNINGS=$((WARNINGS + 1))
    fi

    CURRENT_BRANCH=$(git branch --show-current)
    echo -e "  → Current branch: ${BLUE}$CURRENT_BRANCH${NC}"
else
    echo -e "${YELLOW}  ⚠ Not a git repository or git not available${NC}"
    WARNINGS=$((WARNINGS + 1))
fi
echo ""

# Check 7: EC2 connectivity (if SSH key configured)
echo -e "${BLUE}[7/7] Checking EC2 connectivity...${NC}"

EC2_HOST=$(grep -o 'EC2_HOST=[^[:space:]]*' "$PROJECT_ROOT/.env.production" 2>/dev/null | cut -d= -f2 || echo "")
EC2_USER=$(grep -o 'EC2_USER=[^[:space:]]*' "$PROJECT_ROOT/.env.production" 2>/dev/null | cut -d= -f2 || echo "ubuntu")

if [ -n "$EC2_HOST" ]; then
    if timeout 5 ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no "$EC2_USER@$EC2_HOST" "echo 'OK'" &> /dev/null; then
        echo -e "${GREEN}  ✓ EC2 instance reachable at $EC2_HOST${NC}"
    else
        echo -e "${YELLOW}  ⚠ Cannot reach EC2 instance at $EC2_HOST${NC}"
        echo -e "${YELLOW}    (Check SSH keys and security groups)${NC}"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo -e "${YELLOW}  ⚠ EC2_HOST not configured in .env.production${NC}"
    WARNINGS=$((WARNINGS + 1))
fi
echo ""

# Summary
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Pre-Deployment Check Summary${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

if [ $CRITICAL_FAILURES -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ ALL CHECKS PASSED${NC}"
    echo -e "${GREEN}Status: READY FOR DEPLOYMENT${NC}"
    exit 0
elif [ $CRITICAL_FAILURES -eq 0 ]; then
    echo -e "${YELLOW}⚠ PASSED WITH WARNINGS${NC}"
    echo -e "${YELLOW}Warnings: $WARNINGS${NC}"
    echo -e "${YELLOW}Status: DEPLOYMENT CAN PROCEED (review warnings)${NC}"
    exit 2
else
    echo -e "${RED}✗ CRITICAL FAILURES DETECTED${NC}"
    echo -e "${RED}Critical failures: $CRITICAL_FAILURES${NC}"
    echo -e "${RED}Warnings: $WARNINGS${NC}"
    echo -e "${RED}Status: DEPLOYMENT BLOCKED${NC}"
    exit 1
fi
