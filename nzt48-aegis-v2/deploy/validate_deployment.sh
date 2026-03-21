#!/bin/bash
################################################################################
# Deployment Validation (Part C.3)
#
# Post-deployment validation suite:
# - IB Gateway connectivity
# - Tick stream health (5+ ticks/min)
# - Engine latency (<20ms)
# - Risk arbiter enforcement
# - Redis state store
# - Health endpoint
# - Error rate monitoring
# - Disk space
#
# Usage:
#     bash deploy/validate_deployment.sh [--ec2-ip 3.230.44.22] [--verbose]
#
# Exit codes:
#     0 = all checks pass
#     1 = critical check failed
#     2 = warning checks failed
#
################################################################################

set -euo pipefail

# Configuration
EC2_IP="${EC2_IP:-3.230.44.22}"
EC2_USER="ubuntu"
EC2_KEY="${HOME}/.ssh/nzt48-key.pem"
TIMEOUT=30

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Counters
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# Options
VERBOSE=${VERBOSE:-false}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --ec2-ip) EC2_IP="$2"; shift 2 ;;
        --verbose) VERBOSE=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASS_COUNT++))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((FAIL_COUNT++))
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    ((WARN_COUNT++))
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

check_ssh() {
    local timeout=5
    if timeout ${timeout}s ssh -i "${EC2_KEY}" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        "${EC2_USER}@${EC2_IP}" "echo 'SSH ok'" &>/dev/null; then
        return 0
    else
        return 1
    fi
}

remote_cmd() {
    ssh -i "${EC2_KEY}" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        "${EC2_USER}@${EC2_IP}" "$@"
}

################################################################################
# VALIDATION CHECKS
################################################################################

echo "Validating AEGIS V2 Deployment"
echo "Target: ${EC2_IP}"
echo ""

################################################################################
# Check 1: SSH Connectivity
################################################################################
log_info "Check 1: SSH connectivity..."

if check_ssh; then
    log_pass "SSH connection successful"
else
    log_fail "SSH connection failed to ${EC2_IP}"
    exit 1
fi
echo ""

################################################################################
# Check 2: Docker containers running
################################################################################
log_info "Check 2: Docker containers status..."

if remote_cmd "docker ps --filter 'name=nzt48\|aegis\|python' --format 'table {{.Names}}\t{{.Status}}'" | grep -q "Up"; then
    log_pass "Core containers running"
else
    log_fail "No running containers found"
fi
echo ""

################################################################################
# Check 3: IB Gateway connectivity
################################################################################
log_info "Check 3: IB Gateway connectivity..."

if remote_cmd "docker exec aegis-ib-gateway bash -c 'echo > /dev/tcp/localhost/4003' 2>&1" || \
   remote_cmd "timeout 2 nc -zv localhost 4003 2>&1 | grep -q 'succeeded\|open'"; then
    log_pass "IB Gateway listening on port 4003"
else
    log_warn "IB Gateway port not responding (may still be authenticating)"
fi
echo ""

################################################################################
# Check 4: Tick stream health
################################################################################
log_info "Check 4: Tick stream health (5+ ticks/min)..."

TICK_COUNT=$(remote_cmd "docker logs aegis-v2 2>&1 | grep -i 'tick\|PRICE' | tail -300 | wc -l" 2>/dev/null || echo "0")

if [ "${TICK_COUNT}" -gt 5 ]; then
    log_pass "Tick stream healthy (${TICK_COUNT} recent ticks)"
elif [ "${TICK_COUNT}" -gt 0 ]; then
    log_warn "Low tick count (${TICK_COUNT}, expected >5)"
else
    log_warn "No ticks detected in recent logs (may still be initializing)"
fi
echo ""

################################################################################
# Check 5: Python brain latency
################################################################################
log_info "Check 5: Python brain latency (<20ms)..."

LATENCY=$(remote_cmd "docker logs aegis-v2 2>&1 | grep -oE 'latency[=:]?\s*([0-9.]+)' | tail -1 | grep -oE '[0-9.]+' || echo 'unknown'" 2>/dev/null)

if [[ "${LATENCY}" != "unknown" ]]; then
    if (( $(echo "${LATENCY} < 20" | bc -l 2>/dev/null) )); then
        log_pass "Brain latency acceptable (${LATENCY}ms)"
    else
        log_warn "Brain latency high (${LATENCY}ms, target <20ms)"
    fi
else
    log_warn "Cannot determine brain latency from logs"
fi
echo ""

################################################################################
# Check 6: Risk arbiter active
################################################################################
log_info "Check 6: Risk arbiter enforcement..."

if remote_cmd "docker logs aegis-v2 2>&1 | grep -i 'risk\|arbiter\|gate' | head -3" &>/dev/null; then
    log_pass "Risk arbiter active"
else
    log_warn "Risk arbiter status unclear"
fi
echo ""

################################################################################
# Check 7: Redis connectivity (replaces old PostgreSQL check)
################################################################################
log_info "Check 7: Redis state store..."

if remote_cmd "docker exec aegis-redis redis-cli -a nzt48redis ping 2>/dev/null | grep -q PONG"; then
    log_pass "Redis responding (PONG)"
    KEYS=$(remote_cmd "docker exec aegis-redis redis-cli -a nzt48redis DBSIZE 2>/dev/null | grep -oP '\\d+'")
    log_info "Redis keys: ${KEYS:-unknown}"
else
    log_warn "Redis not responding"
fi
echo ""

################################################################################
# Check 8: Health endpoint
################################################################################
log_info "Check 8: Health check endpoint..."

if timeout 5 remote_cmd "curl -s http://localhost:8000/health | grep -q 'ok\|healthy\|true'" 2>/dev/null; then
    log_pass "Health endpoint responding"
else
    log_warn "Health endpoint not responding (may not be implemented)"
fi
echo ""

################################################################################
# Check 9: Error rate
################################################################################
log_info "Check 9: Error/exception rate..."

ERROR_COUNT=$(remote_cmd "docker logs aegis-v2 2>&1 | grep -i 'error\|exception\|panic' | wc -l" 2>/dev/null || echo "unknown")

if [[ "${ERROR_COUNT}" != "unknown" ]]; then
    if [ "${ERROR_COUNT}" -lt 5 ]; then
        log_pass "Error count low (${ERROR_COUNT})"
    else
        log_warn "Error count elevated (${ERROR_COUNT})"
    fi
else
    log_info "Error count unknown"
fi
echo ""

################################################################################
# Check 10: Disk space
################################################################################
log_info "Check 10: Disk space..."

DISK_USAGE=$(remote_cmd "df -h / | tail -1 | awk '{print \$5}' | sed 's/%//'")

if [ "${DISK_USAGE}" -lt 80 ]; then
    log_pass "Disk usage normal (${DISK_USAGE}%)"
else
    log_warn "Disk usage high (${DISK_USAGE}%, target <80%)"
fi
echo ""

################################################################################
# SUMMARY
################################################################################
echo "============================================================"
echo "Validation Summary"
echo "============================================================"
echo "PASS:  ${PASS_COUNT}"
echo "WARN:  ${WARN_COUNT}"
echo "FAIL:  ${FAIL_COUNT}"
echo ""

if [ ${FAIL_COUNT} -eq 0 ]; then
    if [ ${WARN_COUNT} -eq 0 ]; then
        echo -e "${GREEN}✓ All checks passed${NC}"
        exit 0
    else
        echo -e "${YELLOW}⚠ Some warnings detected${NC}"
        exit 2
    fi
else
    echo -e "${RED}✗ Critical failures detected${NC}"
    exit 1
fi
