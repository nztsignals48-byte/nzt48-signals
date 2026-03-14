#!/usr/bin/env bash
# =============================================================================
# NZT-48 Wave 2 Omega Deployment Script
# =============================================================================
# Deploys the Wave 2 risk engine (14 new/modified files) to the EC2 production
# instance, rebuilds the Docker image, runs the 40-test integration suite
# inside the container, and checks the Go-Live Gate for 8/8 green.
#
# Usage:
#   bash scripts/deploy_wave2.sh
#   NZT48_EC2_HOST=ubuntu@<ip> bash scripts/deploy_wave2.sh
#
# Prerequisites:
#   - SSH key at ~/.ssh/nzt48-key.pem
#   - EC2 instance running with Docker Compose installed
#   - Current directory = project root (nzt48-signals/)
# =============================================================================
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
EC2_HOST="${NZT48_EC2_HOST:-ubuntu@54.242.32.11}"
KEY="$HOME/.ssh/nzt48-key.pem"
REMOTE_DIR="/home/ubuntu/nzt48-signals"
HEALTH_URL="http://localhost:8000/api/health"
GATE_URL="http://localhost:8000/api/gate"
MAX_HEALTH_RETRIES=12
HEALTH_INTERVAL=5

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'  # No Colour

log()  { echo -e "${CYAN}[DEPLOY]${NC} $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $*"; }
fail() { echo -e "${RED}[ FAIL ]${NC} $*"; exit 1; }

# ── Pre-flight checks ───────────────────────────────────────────────────────
log "=== NZT-48 Wave 2 Omega Deployment ==="
log "Target: ${EC2_HOST}:${REMOTE_DIR}"
log ""

# Verify SSH key exists
[[ -f "$KEY" ]] || fail "SSH key not found: $KEY"

# Verify we're in the project root
[[ -f "main.py" ]] || fail "Run from project root (nzt48-signals/)"

# Quick SSH connectivity test
log "Step 0: Testing SSH connectivity..."
ssh -i "$KEY" -o ConnectTimeout=10 -o BatchMode=yes "$EC2_HOST" "echo ok" > /dev/null 2>&1 \
    || fail "Cannot SSH to ${EC2_HOST}. Is the instance running? IP may have changed (no Elastic IP)."
ok "SSH connection verified"

# ── Step 1: Sync source to EC2 ──────────────────────────────────────────────
log ""
log "Step 1: Syncing source to EC2..."
rsync -avz --delete \
    --exclude '.git/' \
    --exclude 'data/' \
    --exclude '.env' \
    --exclude '.env.local' \
    --exclude '.env.production' \
    --exclude 'credentials/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude 'node_modules/' \
    --exclude 'dashboard/frontend/node_modules/' \
    --exclude 'dashboard/frontend/.next/' \
    --exclude '.DS_Store' \
    --exclude '.claude/' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    --exclude 'artifacts/' \
    --exclude '*.tar.gz' \
    --exclude '*.zip' \
    --exclude 'nzt pdfs/' \
    --exclude 'annexes/' \
    --exclude 'reports/' \
    -e "ssh -i $KEY" \
    . "$EC2_HOST:$REMOTE_DIR/"
ok "Source synced"

# ── Steps 2-6: Execute on EC2 ───────────────────────────────────────────────
log ""
log "Step 2-6: Executing on EC2 (build → start → test → gate)..."
log ""

ssh -i "$KEY" "$EC2_HOST" bash << REMOTE
set -euo pipefail

cd ${REMOTE_DIR}

echo ""
echo "=== Step 2: Stopping containers ==="
docker compose down
echo "[OK] Containers stopped"

echo ""
echo "=== Step 3: Rebuilding nzt48 image (--no-cache) ==="
docker compose build --no-cache nzt48
echo "[OK] Image rebuilt"

echo ""
echo "=== Step 4: Starting containers ==="
docker compose up -d
echo "[OK] Containers started"

echo ""
echo "=== Step 5: Waiting for health endpoint ==="
HEALTHY=false
for i in \$(seq 1 ${MAX_HEALTH_RETRIES}); do
    if curl -sf ${HEALTH_URL} > /dev/null 2>&1; then
        echo "[OK] Health check passed (attempt \$i/${MAX_HEALTH_RETRIES})"
        HEALTHY=true
        break
    fi
    echo "[..] Waiting for health... (\$i/${MAX_HEALTH_RETRIES})"
    sleep ${HEALTH_INTERVAL}
done

if [ "\$HEALTHY" != "true" ]; then
    echo "[FAIL] Health check did not pass after ${MAX_HEALTH_RETRIES} attempts"
    echo "Recent logs:"
    docker logs nzt48 --tail 30
    exit 1
fi

echo ""
echo "=== Step 6: Running Wave 2 integration tests inside container ==="
docker exec nzt48 python3 -m pytest tests/test_wave2_integration.py -v --tb=short
TEST_EXIT=\$?
if [ \$TEST_EXIT -ne 0 ]; then
    echo "[FAIL] Integration tests failed (exit code \$TEST_EXIT)"
    exit 1
fi
echo "[OK] Integration tests passed"

echo ""
echo "=== Step 7: Go-Live Gate Check ==="
GATE_RESULT=\$(curl -sf ${GATE_URL} 2>/dev/null || echo '{"error": "gate endpoint unreachable"}')
echo "\$GATE_RESULT" | python3 -m json.tool

# Extract verdict
VERDICT=\$(echo "\$GATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('verdict','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
PASSED=\$(echo "\$GATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('passed',0))" 2>/dev/null || echo "0")
TOTAL=\$(echo "\$GATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")

echo ""
if [ "\$VERDICT" = "GO" ]; then
    echo "============================================"
    echo "  GO-LIVE GATE: GO (\${PASSED}/\${TOTAL})"
    echo "============================================"
else
    echo "============================================"
    echo "  GO-LIVE GATE: NO-GO (\${PASSED}/\${TOTAL})"
    echo "============================================"
    echo "Review failed checks above."
fi

REMOTE

DEPLOY_EXIT=$?

log ""
if [[ $DEPLOY_EXIT -eq 0 ]]; then
    ok "=== Wave 2 Omega deployment complete ==="
else
    fail "Deployment failed (exit code $DEPLOY_EXIT)"
fi
