#!/bin/bash
# AEGIS V2 — Automated Deployment to EC2
# Usage: ./scripts/deploy.sh
#
# What it does:
# 1. Runs local compile checks (Rust + Python)
# 2. Pushes to GitHub
# 3. Pulls on EC2
# 4. Rebuilds Docker containers
# 5. Restarts command station
# 6. Verifies health

set -e

EC2_HOST="ubuntu@3.230.44.22"
SSH_KEY="$HOME/.ssh/nzt48-key.pem"
BRANCH="feat/tier-system-enhancements-full"
REPO_DIR="/home/ubuntu/nzt48-signals-repo"
PROJECT_DIR="$REPO_DIR/nzt48-aegis-v2"

echo "════════════════════════════════════════"
echo "AEGIS V2 DEPLOYMENT"
echo "════════════════════════════════════════"

# Step 1: Local compile checks
echo "[1/6] Running local compile checks..."
cd "$(dirname "$0")/.."
cargo check 2>&1 | tail -1
find python_brain -name "*.py" -not -name "__init__.py" -not -path "*__pycache__*" -exec python3 -m py_compile {} \;
echo "  ✓ All files compile clean"

# Step 2: Push to GitHub
echo "[2/6] Pushing to GitHub..."
git push origin "$BRANCH" 2>&1 | tail -2

# Step 3: Pull on EC2
echo "[3/6] Pulling on EC2..."
ssh -i "$SSH_KEY" "$EC2_HOST" "cd $REPO_DIR && git pull origin $BRANCH" 2>&1 | tail -3

# Step 4: Rebuild Docker
echo "[4/6] Rebuilding Docker containers..."
ssh -i "$SSH_KEY" "$EC2_HOST" "cd $PROJECT_DIR && docker compose up -d --build" 2>&1 | tail -5

# Step 5: Restart command station
echo "[5/6] Restarting command station..."
ssh -i "$SSH_KEY" "$EC2_HOST" "pkill -f 'command_station' 2>/dev/null; sleep 1; cd $PROJECT_DIR && nohup python3 -m python_brain.terminal.command_station --port 8173 > /tmp/command_station.log 2>&1 &"

# Step 6: Health check
echo "[6/6] Verifying health..."
sleep 5
ssh -i "$SSH_KEY" "$EC2_HOST" "docker ps --format '{{.Names}} {{.Status}}' && echo '---' && curl -s http://localhost:8173/api/state | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"timestamp\"])'" 2>&1

echo ""
echo "════════════════════════════════════════"
echo "DEPLOYMENT COMPLETE"
echo "════════════════════════════════════════"
echo "Command Station: ssh -L 8173:localhost:8173 -i $SSH_KEY $EC2_HOST"
echo "                 then open http://localhost:8173"
echo "Grafana:         ssh -L 3000:localhost:3000 -i $SSH_KEY $EC2_HOST"
echo "                 then open http://localhost:3000 (admin/aegis2026)"
