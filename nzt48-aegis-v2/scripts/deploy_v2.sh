#!/usr/bin/env bash
# AEGIS V2 — Deploy to EC2
# Usage: bash scripts/deploy_v2.sh [rebuild|sync|stop]
set -euo pipefail

EC2_HOST="${NZT48_EC2_HOST:-ubuntu@3.230.44.22}"
SSH_KEY="$HOME/.ssh/nzt48-key.pem"
REMOTE_DIR="/home/ubuntu/nzt48-aegis-v2"
MODE="${1:-rebuild}"

ssh_cmd() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$EC2_HOST" "$@"; }

echo "╔══════════════════════════════════════════╗"
echo "║  AEGIS V2 — Deploy ($MODE)              ║"
echo "║  Target: $EC2_HOST                      ║"
echo "╚══════════════════════════════════════════╝"

# Step 1: SSH connectivity
echo "[1/5] Testing SSH..."
if ! ssh_cmd "echo ok" >/dev/null 2>&1; then
    echo "FATAL: Cannot SSH to $EC2_HOST"
    exit 1
fi
echo "  SSH: OK"

# Step 2: Rsync source code
echo "[2/5] Syncing source..."
rsync -avz --delete \
    --exclude='target/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='.git/' \
    --exclude='docs/' \
    --exclude='dead_letter/' \
    --exclude='events/*.ndjson' \
    --exclude='.DS_Store' \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    "$(dirname "$0")/../" "$EC2_HOST:$REMOTE_DIR/"
echo "  Sync: OK"

if [ "$MODE" = "sync" ]; then
    echo "Sync-only mode. Done."
    exit 0
fi

if [ "$MODE" = "stop" ]; then
    echo "[3/5] Stopping services..."
    ssh_cmd "cd $REMOTE_DIR && docker compose down"
    echo "  Stopped."
    exit 0
fi

# Step 3: Build
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "[3/5] Building (GIT_SHA=$GIT_SHA)..."
ssh_cmd "cd $REMOTE_DIR && GIT_SHA=$GIT_SHA docker compose build aegis-v2"
echo "  Build: OK"

# Step 4: Start services
echo "[4/5] Starting services..."
ssh_cmd "cd $REMOTE_DIR && docker compose up -d"
echo "  Started."

# Step 5: Verify
echo "[5/5] Verifying..."
sleep 5

# Check containers are running
RUNNING=$(ssh_cmd "cd $REMOTE_DIR && docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null" || true)
echo "  Containers:"
echo "$RUNNING" | while IFS= read -r line; do echo "    $line"; done

# Check engine logs
echo ""
echo "  Engine logs (last 20 lines):"
ssh_cmd "docker logs aegis-v2 --tail 20 2>&1" | while IFS= read -r line; do echo "    $line"; done

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Deploy complete. Paper trading active.  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Monitor: ssh -i $SSH_KEY $EC2_HOST 'docker logs -f aegis-v2'"
echo "Stop:    bash scripts/deploy_v2.sh stop"
