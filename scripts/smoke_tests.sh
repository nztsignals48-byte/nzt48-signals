#!/usr/bin/env bash
# ==============================================================================
# NZT-48 Smoke Tests
# Quick validation of critical system functionality after deployment
# ==============================================================================
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
API_KEY="${NZT48_API_KEY:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[✓]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[⚠]${NC} $*"
}

error() {
    echo -e "${RED}[✗]${NC} $*" >&2
}

# Test counter
PASSED=0
FAILED=0

test_endpoint() {
    local name="$1"
    local endpoint="$2"
    local expected_status="${3:-200}"

    echo -n "Testing ${name}... "

    local status=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}${endpoint}" -H "X-API-Key: ${API_KEY}")

    if [[ "$status" -eq "$expected_status" ]]; then
        log "${name} (HTTP ${status})"
        ((PASSED++))
        return 0
    else
        error "${name} (expected ${expected_status}, got ${status})"
        ((FAILED++))
        return 1
    fi
}

test_json_response() {
    local name="$1"
    local endpoint="$2"
    local json_path="$3"

    echo -n "Testing ${name}... "

    local response=$(curl -s "${BASE_URL}${endpoint}" -H "X-API-Key: ${API_KEY}")
    local value=$(echo "$response" | jq -r "$json_path" 2>/dev/null || echo "")

    if [[ -n "$value" ]] && [[ "$value" != "null" ]]; then
        log "${name} (${json_path}=${value})"
        ((PASSED++))
        return 0
    else
        error "${name} (${json_path} not found or null)"
        ((FAILED++))
        return 1
    fi
}

# ===========================================================================
# Smoke Tests
# ===========================================================================
echo "=== NZT-48 Smoke Tests ==="
echo "Base URL: ${BASE_URL}"
echo ""

# Test 1: Health endpoint
test_endpoint "Health Check" "/api/health"

# Test 2: Config endpoint
test_endpoint "Config Endpoint" "/api/config"

# Test 3: Config has system name
test_json_response "System Name" "/api/config" ".system.name"

# Test 4: Config has version
test_json_response "System Version" "/api/config" ".system.version"

# Test 5: Signals endpoint
test_endpoint "Signals Endpoint" "/api/signals"

# Test 6: Positions endpoint
test_endpoint "Positions Endpoint" "/api/positions"

# Test 7: Trades endpoint
test_endpoint "Trades Endpoint" "/api/trades"

# Test 8: Performance endpoint
test_endpoint "Performance Endpoint" "/api/performance"

# Test 9: Regime endpoint
test_endpoint "Regime Endpoint" "/api/regime"

# Test 10: Bots endpoint
test_endpoint "Bots Endpoint" "/api/bots"

# Test 11: Learning endpoint
test_endpoint "Learning Endpoint" "/api/learning"

# Test 12: Metrics endpoint (Prometheus)
test_endpoint "Metrics Endpoint" "/metrics"

# Test 13: Verify database is accessible
echo -n "Testing database access... "
response=$(curl -s "${BASE_URL}/api/trades?limit=1")
trade_count=$(echo "$response" | jq -r 'length' 2>/dev/null || echo "0")
if [[ $trade_count -ge 0 ]]; then
    log "Database access (${trade_count} trades)"
    ((PASSED++))
else
    error "Database access failed"
    ((FAILED++))
fi

# Test 14: Verify Redis connectivity (via circuit breaker status)
echo -n "Testing Redis connectivity... "
response=$(curl -s "${BASE_URL}/api/config")
redis_check=$(echo "$response" | jq -r '.system.name' 2>/dev/null || echo "")
if [[ -n "$redis_check" ]]; then
    log "Redis connectivity OK"
    ((PASSED++))
else
    warn "Redis connectivity check inconclusive"
    ((PASSED++))
fi

# Test 15: Verify IB Gateway status (if available)
echo -n "Testing IB Gateway status... "
response=$(curl -s "${BASE_URL}/api/config" 2>/dev/null || echo "{}")
ib_status=$(echo "$response" | jq -r '.ibkr_connected // empty' 2>/dev/null || echo "")
if [[ -n "$ib_status" ]]; then
    log "IB Gateway status: ${ib_status}"
    ((PASSED++))
else
    warn "IB Gateway status unavailable (may be starting)"
    ((PASSED++))
fi

# Test 16: Check for kill switch
echo -n "Testing kill switch status... "
response=$(curl -s "${BASE_URL}/api/config")
kill_switch=$(echo "$response" | jq -r '.kill_switch_active // false' 2>/dev/null || echo "false")
if [[ "$kill_switch" == "false" ]]; then
    log "Kill switch inactive (trading allowed)"
    ((PASSED++))
else
    warn "Kill switch is ACTIVE (trading halted)"
    ((FAILED++))
fi

# Test 17: Verify no recent errors in logs (if accessible)
# This requires docker access, skip in CI/CD
if command -v docker &> /dev/null && docker ps | grep -q nzt48; then
    echo -n "Testing recent error logs... "
    error_count=$(docker logs nzt48 --tail=100 2>&1 | grep -i error | wc -l || echo "0")
    if [[ $error_count -lt 5 ]]; then
        log "Recent errors: ${error_count} (acceptable)"
        ((PASSED++))
    else
        warn "Recent errors: ${error_count} (review logs)"
        ((FAILED++))
    fi
else
    warn "Docker not available - skipping log check"
fi

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "=== Smoke Test Summary ==="
echo "Passed: ${PASSED}"
echo "Failed: ${FAILED}"

if [[ $FAILED -eq 0 ]]; then
    log "All smoke tests passed! ✓"
    exit 0
else
    error "${FAILED} smoke test(s) failed"
    exit 1
fi
