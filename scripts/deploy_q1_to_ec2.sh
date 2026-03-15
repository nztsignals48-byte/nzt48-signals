#!/bin/bash
#
# Phase Q1 Deployment Script
# ==========================
# Deploys Q1 Quick Wins (+1.3 Sharpe) to EC2 production
#
# Usage: bash scripts/deploy_q1_to_ec2.sh
#

set -e  # Exit on error

echo "========================================"
echo "Phase Q1 Deployment to EC2"
echo "========================================"
echo ""

# Configuration
EC2_HOST="ubuntu@3.230.44.22"
SSH_KEY="$HOME/.ssh/nzt48-key.pem"
REMOTE_DIR="/home/ubuntu/nzt48-signals"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Step 1: Pre-flight checks${NC}"
echo "----------------------------"

# Check SSH key exists
if [ ! -f "$SSH_KEY" ]; then
    echo -e "${RED}✗ SSH key not found: $SSH_KEY${NC}"
    exit 1
fi
echo -e "${GREEN}✓ SSH key found${NC}"

# Check git status
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${YELLOW}⚠ Uncommitted changes detected. Commit first!${NC}"
    git status --short
    exit 1
fi
echo -e "${GREEN}✓ Git clean (all changes committed)${NC}"

# Test connection
echo "Testing EC2 connection..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$EC2_HOST" "echo 'Connection OK'" &>/dev/null; then
    echo -e "${RED}✗ Cannot connect to EC2${NC}"
    exit 1
fi
echo -e "${GREEN}✓ EC2 connection successful${NC}"
echo ""

echo -e "${YELLOW}Step 2: Sync code to EC2${NC}"
echo "----------------------------"

# Rsync code to EC2 (exclude venv, data, logs)
rsync -avz --delete \
    --exclude 'venv/' \
    --exclude '.venv/' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'dashboard/' \
    --exclude 'nzt48-aegis-v2/' \
    --exclude 'nzt48-rust/' \
    --exclude 'annexes/' \
    --exclude 'reports/' \
    -e "ssh -i $SSH_KEY" \
    ./ "$EC2_HOST:$REMOTE_DIR/"

echo -e "${GREEN}✓ Code synced to EC2${NC}"
echo ""

echo -e "${YELLOW}Step 3: Rebuild Docker image${NC}"
echo "----------------------------"

ssh -i "$SSH_KEY" "$EC2_HOST" << 'ENDSSH'
cd /home/ubuntu/nzt48-signals
echo "Building Docker image..."
docker compose build nzt48
echo "✓ Docker image built"
ENDSSH

echo -e "${GREEN}✓ Docker image rebuilt${NC}"
echo ""

echo -e "${YELLOW}Step 4: Restart NZT48 container${NC}"
echo "----------------------------"

ssh -i "$SSH_KEY" "$EC2_HOST" << 'ENDSSH'
cd /home/ubuntu/nzt48-signals
echo "Stopping old container..."
docker compose stop nzt48
echo "Removing old container..."
docker compose rm -f nzt48
echo "Starting new container..."
docker compose up -d nzt48
echo "✓ Container restarted"
ENDSSH

echo -e "${GREEN}✓ NZT48 container restarted${NC}"
echo ""

echo -e "${YELLOW}Step 5: Verify deployment${NC}"
echo "----------------------------"

# Wait for container to start
echo "Waiting 5 seconds for container startup..."
sleep 5

# Check container status
ssh -i "$SSH_KEY" "$EC2_HOST" << 'ENDSSH'
cd /home/ubuntu/nzt48-signals
echo "Container status:"
docker compose ps nzt48
echo ""
echo "Last 30 log lines:"
docker logs nzt48 --tail 30
ENDSSH

echo ""
echo -e "${GREEN}========================================"
echo "✓ Phase Q1 Deployment Complete"
echo "========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Monitor logs: ssh -i $SSH_KEY $EC2_HOST 'docker logs nzt48 -f'"
echo "2. Check paper trades: ssh -i $SSH_KEY $EC2_HOST 'docker exec nzt48 python scripts/check_paper_trades.py'"
echo "3. Validate 1 week, 100-Trade Gate (WR ≥ 40%)"
echo ""
echo "Expected improvement: +1.3 Sharpe (0.3-0.5% daily net)"
echo ""
