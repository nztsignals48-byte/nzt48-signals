#!/bin/bash
# Q1-Q4 Deployment Orchestration Script
# Deploys completed phases to EC2 in order

set -e

PROJECT_DIR="/Users/rr/nzt48-signals"
EC2_HOST="ubuntu@3.230.44.22"
EC2_DIR="/home/ubuntu/nzt48-signals"
SSH_KEY="$HOME/.ssh/nzt48-key.pem"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
  echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"
}

warn() {
  echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARNING:${NC} $1"
}

error() {
  echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $1"
  exit 1
}

# ----------------------------------------------------------------
# PHASE SELECTION
# ----------------------------------------------------------------
PHASE=${1:-"all"}

if [[ ! "$PHASE" =~ ^(q1|q2|q3|q4|all)$ ]]; then
  error "Usage: $0 [q1|q2|q3|q4|all]"
fi

log "Deploying Phase: $PHASE"

# ----------------------------------------------------------------
# PRE-FLIGHT CHECKS
# ----------------------------------------------------------------
log "Running pre-flight checks..."

# Check SSH connectivity
if ! ssh -i "$SSH_KEY" "$EC2_HOST" "echo 'SSH OK'" &> /dev/null; then
  error "Cannot SSH to EC2 ($EC2_HOST)"
fi

# Check git status
cd "$PROJECT_DIR"
if [[ -n $(git status -s) ]]; then
  warn "Uncommitted changes detected. Commit before deploying."
  read -p "Continue anyway? (y/n) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

# ----------------------------------------------------------------
# BUILD DOCKER IMAGE
# ----------------------------------------------------------------
log "Building Docker image..."
docker build -t nzt48:latest . || error "Docker build failed"

# Tag with phase
docker tag nzt48:latest nzt48:$PHASE

log "Docker image built: nzt48:$PHASE"

# ----------------------------------------------------------------
# SYNC CODE TO EC2
# ----------------------------------------------------------------
log "Syncing code to EC2..."

rsync -avz --progress \
  -e "ssh -i $SSH_KEY" \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude 'data/' \
  --exclude 'dashboard/' \
  --exclude 'reports/' \
  --exclude 'nzt48-aegis-v2/target/' \
  "$PROJECT_DIR/" \
  "$EC2_HOST:$EC2_DIR/" || error "Rsync failed"

log "Code synced to EC2"

# ----------------------------------------------------------------
# DEPLOY TO EC2
# ----------------------------------------------------------------
log "Deploying to EC2..."

ssh -i "$SSH_KEY" "$EC2_HOST" << 'EOSSH'
set -e

cd /home/ubuntu/nzt48-signals

echo "Stopping existing containers..."
docker compose down || true

echo "Rebuilding Docker image on EC2..."
docker compose build

echo "Starting containers..."
docker compose up -d

echo "Waiting 10 seconds for startup..."
sleep 10

echo "Checking container status..."
docker compose ps

echo "Checking logs (last 50 lines)..."
docker logs nzt48 --tail 50

echo "✓ Deployment complete"
EOSSH

log "✓ Deployment to EC2 complete"

# ----------------------------------------------------------------
# POST-DEPLOYMENT VERIFICATION
# ----------------------------------------------------------------
log "Running post-deployment verification..."

ssh -i "$SSH_KEY" "$EC2_HOST" << 'EOSSH'
set -e

cd /home/ubuntu/nzt48-signals

echo "Verifying paper trading active..."
if docker logs nzt48 --tail 100 | grep -qi "paper\|connected\|scanning"; then
  echo "✓ Paper trading appears active"
else
  echo "⚠ Paper trading status unclear - check logs manually"
fi

echo "Checking IB Gateway..."
if docker ps | grep -q ib-gateway; then
  echo "✓ IB Gateway container running"
else
  echo "⚠ IB Gateway not running"
fi
EOSSH

# ----------------------------------------------------------------
# SUMMARY
# ----------------------------------------------------------------
log "======================================="
log "DEPLOYMENT SUMMARY"
log "======================================="
log "Phase: $PHASE"
log "EC2: $EC2_HOST"
log "Time: $(date)"
log ""
log "Next Steps:"
log "1. SSH to EC2: ssh -i $SSH_KEY $EC2_HOST"
log "2. Check logs: docker logs nzt48 -f"
log "3. Monitor trades: docker exec -it nzt48 python3 -c 'from core.outcomes_engine import OutcomesEngine; oe = OutcomesEngine(); print(oe.get_recent_trades(10))'"
log "4. Check dashboard: http://${EC2_HOST}:8000/dashboard"
log ""
