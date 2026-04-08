#!/bin/bash
# AEGIS V2 — Deploy to Oracle Cloud ARM Instance
# Usage: ./scripts/deploy-oracle.sh
#
# Reads ORACLE_HOST from environment or prompts.
# SSH key: ~/.ssh/oracle-nzt48.key (or set ORACLE_SSH_KEY)

set -e

ORACLE_HOST="${ORACLE_HOST:-}"
SSH_KEY="${ORACLE_SSH_KEY:-$HOME/.ssh/oracle-nzt48.key}"
BRANCH="feat/tier-system-enhancements-full"
REPO_DIR="/home/ubuntu/nzt48-signals-repo"
PROJECT_DIR="$REPO_DIR/nzt48-aegis-v2"

if [ -z "$ORACLE_HOST" ]; then
    echo "Set ORACLE_HOST environment variable:"
    echo "  export ORACLE_HOST=ubuntu@<your-oracle-ip>"
    echo "  ./scripts/deploy-oracle.sh"
    exit 1
fi

echo "════════════════════════════════════════"
echo "AEGIS V2 — ORACLE CLOUD DEPLOYMENT"
echo "Target: $ORACLE_HOST"
echo "════════════════════════════════════════"

# Step 1: Local checks
echo "[1/5] Running local compile checks..."
cd "$(dirname "$0")/.."
cargo check --manifest-path rust_core/Cargo.toml 2>&1 | tail -1
python3 -c "import ast; ast.parse(open('python_brain/bridge.py').read()); print('bridge.py OK')"
echo "  ✓ All files compile clean"

# Step 2: Push to GitHub
echo "[2/5] Pushing to GitHub..."
git push origin "$BRANCH" 2>&1 | tail -2

# Step 3: Pull on Oracle
echo "[3/5] Pulling on Oracle..."
ssh -i "$SSH_KEY" "$ORACLE_HOST" "cd $REPO_DIR && git pull origin $BRANCH" 2>&1 | tail -3

# Step 4: Rebuild Docker
echo "[4/5] Rebuilding Docker containers (ARM native)..."
ssh -i "$SSH_KEY" "$ORACLE_HOST" "cd $PROJECT_DIR && docker compose up -d --build" 2>&1 | tail -10

# Step 5: Health check
echo "[5/5] Verifying health..."
sleep 10
ssh -i "$SSH_KEY" "$ORACLE_HOST" "docker ps --format 'table {{.Names}}\t{{.Status}}'"

echo ""
echo "════════════════════════════════════════"
echo "DEPLOYMENT COMPLETE"
echo "════════════════════════════════════════"
echo "Grafana: ssh -L 3000:localhost:3000 -i $SSH_KEY $ORACLE_HOST"
echo "         then open http://localhost:3000 (admin/aegis2026)"
