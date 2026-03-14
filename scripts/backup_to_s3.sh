#!/bin/bash
# NZT-48 Data Backup — copies critical data to S3
#
# Crontab entry (run daily at 22:00 UTC, after EOD):
#   0 22 * * * /home/ubuntu/nzt48-signals/scripts/backup_to_s3.sh >> /var/log/nzt48-backup.log 2>&1
#
# Backs up:
#   - SQLite database (trade history, outcomes)
#   - outcomes.jsonl (ML training data — irreplaceable)
#   - ml_predictions.jsonl (ML predictions log)
#   - Redis AOF dump (Chandelier state, circuit breakers, kill switch)
#
# Requires: aws cli configured with s3:PutObject permission
# Setup: aws configure (or IAM instance role)
#
# Restoration test command (verify backup integrity periodically):
#   LATEST=$(aws s3 ls s3://${NZT48_BACKUP_BUCKET:-nzt48-backups}/ --recursive | sort | tail -1 | awk '{print $4}')
#   aws s3 cp "s3://${NZT48_BACKUP_BUCKET:-nzt48-backups}/$LATEST" /tmp/nzt48-restore-test.tar.gz
#   mkdir -p /tmp/nzt48-restore-test && tar -xzf /tmp/nzt48-restore-test.tar.gz -C /tmp/nzt48-restore-test
#   sqlite3 /tmp/nzt48-restore-test/nzt48.db "SELECT COUNT(*) FROM virtual_trades;"
#   wc -l /tmp/nzt48-restore-test/outcomes.jsonl
#   echo "Restore test OK" && rm -rf /tmp/nzt48-restore-test /tmp/nzt48-restore-test.tar.gz

set -euo pipefail

BUCKET="${NZT48_BACKUP_BUCKET:-nzt48-backups}"
CONTAINER="nzt48"
BACKUP_DIR="/tmp/nzt48-backup-$(date +%Y%m%d-%H%M%S)"
DATE_PREFIX="$(date +%Y/%m/%d)"

echo "=== NZT-48 Backup: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

mkdir -p "$BACKUP_DIR"

# 1. SQLite database (copy from Docker volume)
echo "[1/4] Backing up SQLite database..."
docker cp "$CONTAINER:/app/data/nzt48.db" "$BACKUP_DIR/nzt48.db" 2>/dev/null && \
    echo "  OK: nzt48.db" || echo "  SKIP: nzt48.db not found"

# 2. Outcomes JSONL (ML training data — irreplaceable)
echo "[2/4] Backing up outcomes.jsonl..."
docker cp "$CONTAINER:/app/data/outcomes.jsonl" "$BACKUP_DIR/outcomes.jsonl" 2>/dev/null && \
    echo "  OK: outcomes.jsonl" || echo "  SKIP: outcomes.jsonl not found"

# 3. ML predictions log
docker cp "$CONTAINER:/app/data/ml_predictions.jsonl" "$BACKUP_DIR/ml_predictions.jsonl" 2>/dev/null || true

# 4. Redis AOF (Chandelier state, circuit breakers, kill switch)
echo "[3/5] Backing up Redis AOF..."
docker exec nzt48-redis redis-cli -a nzt48redis BGSAVE 2>/dev/null || true
sleep 2
docker cp "nzt48-redis:/data/appendonly.aof" "$BACKUP_DIR/redis-appendonly.aof" 2>/dev/null && \
    echo "  OK: redis AOF" || echo "  SKIP: Redis AOF not found"

# 5. Config (strategy parameters — needed for reproducibility)
echo "[4/5] Backing up config..."
docker cp "$CONTAINER:/app/config" "$BACKUP_DIR/config" 2>/dev/null && \
    echo "  OK: config/" || echo "  SKIP: config not found"

# 6. Compress and upload
echo "[5/5] Compressing and uploading to S3..."
ARCHIVE="/tmp/nzt48-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$ARCHIVE" -C "$BACKUP_DIR" .

# Upload to S3
if command -v aws &>/dev/null; then
    aws s3 cp "$ARCHIVE" "s3://$BUCKET/$DATE_PREFIX/$(basename $ARCHIVE)" --storage-class STANDARD_IA
    echo "  Uploaded to s3://$BUCKET/$DATE_PREFIX/$(basename $ARCHIVE)"
else
    echo "  WARNING: aws cli not installed. Backup saved locally at $ARCHIVE"
    echo "  Install: sudo apt install awscli && aws configure"
fi

# Cleanup
rm -rf "$BACKUP_DIR"
# Keep local backup for 7 days
find /tmp -name "nzt48-backup-*.tar.gz" -mtime +7 -delete 2>/dev/null || true

echo "=== Backup complete ==="
