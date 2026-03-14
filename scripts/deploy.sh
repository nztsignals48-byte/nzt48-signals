#!/bin/bash
# =============================================================================
# NZT-48 Auto-Sync Deploy Script
# =============================================================================
# One command: bash scripts/deploy.sh
# Syncs all local changes to EC2, restarts containers, verifies health.
#
# Prerequisites:
#   - SSH key at ~/.ssh/nzt48-key.pem
#   - EC2 instance running at the configured IP
#   - Docker Compose v2 on EC2
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
EC2_HOST="ubuntu@3.230.44.22"
SSH_KEY="$HOME/.ssh/nzt48-key.pem"
REMOTE_DIR="/home/ubuntu/nzt48-signals"
CONTAINER_ENGINE="nzt48"
CONTAINER_DASHBOARD="nzt48-dashboard"
# Docker Compose SERVICE names (not container names)
SERVICE_ENGINE="nzt48"
SERVICE_DASHBOARD="dashboard"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

log_step() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

log_ok() {
    echo -e "${GREEN}  ✅ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}  ⚠️  $1${NC}"
}

log_fail() {
    echo -e "${RED}  ❌ $1${NC}"
}

ssh_cmd() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$EC2_HOST" "$@"
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
log_step "PRE-FLIGHT CHECKS"

if [ ! -f "$SSH_KEY" ]; then
    log_fail "SSH key not found: $SSH_KEY"
    exit 1
fi
log_ok "SSH key found"

# Test SSH connection
if ! ssh_cmd "echo ok" &>/dev/null; then
    log_fail "Cannot reach EC2 at $EC2_HOST"
    exit 1
fi
log_ok "EC2 reachable"

# ---------------------------------------------------------------------------
# Step 1: Rsync local → EC2
# ---------------------------------------------------------------------------
log_step "STEP 1: RSYNC LOCAL → EC2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -avz --progress --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude '.env.local' \
    --exclude 'data/*.db' \
    --exclude 'data/*.db-journal' \
    --exclude 'data/*.db-wal' \
    --exclude 'node_modules' \
    --exclude '.next' \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude 'learning/outcomes.jsonl' \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    "$SCRIPT_DIR/" "$EC2_HOST:$REMOTE_DIR/"

log_ok "Files synced to EC2"

# ---------------------------------------------------------------------------
# Step 2: Docker Compose rebuild + restart
# ---------------------------------------------------------------------------
log_step "STEP 2: REBUILD & RESTART CONTAINERS"

# Rebuild engine container (service name, not container name)
ssh_cmd "cd $REMOTE_DIR && docker compose build $SERVICE_ENGINE"
log_ok "Engine container rebuilt"

# Rebuild dashboard container (service name = 'dashboard', container = 'nzt48-dashboard')
ssh_cmd "cd $REMOTE_DIR && docker compose build $SERVICE_DASHBOARD"
log_ok "Dashboard container rebuilt"

# Restart both (use service names)
ssh_cmd "cd $REMOTE_DIR && docker compose up -d $SERVICE_ENGINE $SERVICE_DASHBOARD"
log_ok "Containers restarted"

# ---------------------------------------------------------------------------
# Step 3: Health check (wait up to 60 seconds)
# ---------------------------------------------------------------------------
log_step "STEP 3: HEALTH CHECK"

echo "  Waiting for engine to start..."
HEALTH_OK=false
for i in $(seq 1 12); do
    sleep 5
    HEALTH=$(ssh_cmd "curl -s http://localhost:8000/api/health 2>/dev/null || echo '{}'")
    STATUS=$(echo "$HEALTH" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")

    if [ "$STATUS" = "ok" ] || [ "$STATUS" = "running" ] || [ "$STATUS" = "healthy" ]; then
        HEALTH_OK=true
        break
    fi
    echo "  ... attempt $i/12 (status: $STATUS)"
done

if $HEALTH_OK; then
    log_ok "Engine health check PASSED (status: $STATUS)"
else
    log_warn "Engine health check inconclusive after 60s (status: $STATUS)"
    log_warn "Check logs: ssh EC2 'docker logs $CONTAINER_ENGINE --tail 50'"
fi

# Dashboard health
DASH_STATUS=$(ssh_cmd "curl -s -o /dev/null -w '%{http_code}' http://localhost:3001/ 2>/dev/null || echo '000'")
if [ "$DASH_STATUS" = "200" ]; then
    log_ok "Dashboard health check PASSED (HTTP $DASH_STATUS)"
else
    log_warn "Dashboard returned HTTP $DASH_STATUS (may still be starting)"
fi

# ---------------------------------------------------------------------------
# Step 4: Quick verification
# ---------------------------------------------------------------------------
log_step "STEP 4: QUICK VERIFICATION"

# Check last few log lines for signs of life
RECENT_LOGS=$(ssh_cmd "docker logs $CONTAINER_ENGINE --tail 10 2>&1" || echo "Could not fetch logs")
echo "$RECENT_LOGS" | head -10

# Check if S15 priority path is registered
if echo "$RECENT_LOGS" | grep -qi "S15\|strategy.*loaded\|scan.*cycle"; then
    log_ok "Engine shows signs of active scanning"
else
    log_warn "No scan activity detected in last 10 log lines (may need more time)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log_step "DEPLOY COMPLETE"
echo ""
echo -e "  ${GREEN}Engine:${NC}    http://3.230.44.22:8000/api/health"
echo -e "  ${GREEN}Dashboard:${NC} http://3.230.44.22:3001/"
echo -e "  ${GREEN}Logs:${NC}      ssh EC2 'docker logs $CONTAINER_ENGINE --tail 50'"
echo ""
echo -e "  ${YELLOW}Verify S15 fires:${NC}  ssh EC2 'docker logs nzt48 --tail 200 | grep S15'"
echo -e "  ${YELLOW}Verify ratchet:${NC}    ssh EC2 'docker logs nzt48 | grep RATCHET'"
echo -e "  ${YELLOW}Verify S16:${NC}        ssh EC2 'docker logs nzt48 | grep S16'"
echo ""
