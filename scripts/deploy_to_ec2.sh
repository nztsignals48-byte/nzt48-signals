#!/bin/bash
# NZT-48 Deployment Script — Institutional Plan Implementation
# Run this after EC2 instance is accessible
# Usage: bash scripts/deploy_to_ec2.sh

set -e

EC2_HOST="ubuntu@3.230.44.22"
SSH_KEY="$HOME/.ssh/nzt48-key.pem"
REMOTE_DIR="/home/ubuntu/nzt48-signals"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== NZT-48 Deployment: Institutional Plan ==="
echo "Local:  $LOCAL_DIR"
echo "Remote: $EC2_HOST:$REMOTE_DIR"
echo ""

# Test SSH
echo "[1/5] Testing SSH connection..."
ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$EC2_HOST" "echo 'SSH OK'" || {
    echo "ERROR: Cannot reach EC2. Check instance status in AWS Console."
    echo "  aws ec2 describe-instances --filters 'Name=tag:Name,Values=*nzt*'"
    exit 1
}

# Sync files
echo "[2/5] Syncing files to EC2..."
rsync -avz --progress \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '*.pyc' \
    --exclude 'data/' \
    --exclude 'credentials/' \
    --exclude '.env' \
    --exclude '.env.production' \
    --exclude 'dashboard/' \
    -e "ssh -i $SSH_KEY" \
    "$LOCAL_DIR/" "$EC2_HOST:$REMOTE_DIR/"

echo "[3/5] Building Docker containers..."
ssh -i "$SSH_KEY" "$EC2_HOST" "cd $REMOTE_DIR && docker compose build nzt48"

echo "[4/5] Starting services..."
ssh -i "$SSH_KEY" "$EC2_HOST" "cd $REMOTE_DIR && docker compose up -d"

echo "[5/5] Checking status..."
sleep 5
ssh -i "$SSH_KEY" "$EC2_HOST" "cd $REMOTE_DIR && docker compose ps && echo '---LOGS---' && docker logs nzt48 --tail 30"

# Phase 43: Generate strategy PDF and sync to desktop
echo "[6/6] Generating strategy PDF..."
python3 scripts/generate_strategy_pdf.py 2>/dev/null || echo "  (PDF generation skipped — run manually)"
echo "Strategy PDF synced to ~/Desktop/nzt/"

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Next steps:"
echo "  1. Check logs: ssh -i $SSH_KEY $EC2_HOST 'docker logs nzt48 --tail 50'"
echo "  2. Run Go/No-Go: ssh -i $SSH_KEY $EC2_HOST 'cd $REMOTE_DIR && docker exec nzt48 python3 scripts/sprint6_live_gate.py'"
echo "  3. Run Audit:    ssh -i $SSH_KEY $EC2_HOST 'cd $REMOTE_DIR && docker exec nzt48 python3 scripts/lookahead_audit.py'"
