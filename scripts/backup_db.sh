#!/usr/bin/env bash
# NZT-48 Database Backup Script
# Backs up SQLite databases with integrity check, compression, and rotation.
#
# Usage: ./scripts/backup_db.sh [--s3]
# Schedule: daily via cron or APScheduler at 03:00 UTC
#
# Keeps last 7 daily backups locally. With --s3, also uploads to S3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_ROOT/data"
BACKUP_DIR="$PROJECT_ROOT/backups"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
KEEP_DAYS=7

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "=== NZT-48 DB BACKUP — $TIMESTAMP ==="

# Find all SQLite databases
DB_FILES=$(find "$DATA_DIR" -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" 2>/dev/null)

if [ -z "$DB_FILES" ]; then
    echo "WARNING: No database files found in $DATA_DIR"
    # Also back up JSONL data files
    for jsonl in "$DATA_DIR"/*.jsonl; do
        if [ -f "$jsonl" ]; then
            BASENAME=$(basename "$jsonl")
            DEST="$BACKUP_DIR/${BASENAME%.jsonl}_${TIMESTAMP}.jsonl.gz"
            gzip -c "$jsonl" > "$DEST"
            echo "  BACKED UP: $BASENAME → $(basename "$DEST")"
        fi
    done
else
    for DB in $DB_FILES; do
        BASENAME=$(basename "$DB")
        BACKUP_NAME="${BASENAME%.db}_${TIMESTAMP}.db"
        BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

        echo "--- Backing up: $BASENAME ---"

        # 1. Integrity check
        INTEGRITY=$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>&1)
        if [ "$INTEGRITY" != "ok" ]; then
            echo "  WARNING: Integrity check failed for $BASENAME: $INTEGRITY"
            echo "  Proceeding with backup anyway..."
        else
            echo "  Integrity: OK"
        fi

        # 2. Backup using SQLite .backup command (safe, consistent)
        sqlite3 "$DB" ".backup '$BACKUP_PATH'"
        echo "  Backup created: $BACKUP_NAME"

        # 3. Compress
        gzip "$BACKUP_PATH"
        echo "  Compressed: ${BACKUP_NAME}.gz ($(du -h "$BACKUP_PATH.gz" | cut -f1))"
    done

    # Also back up critical JSONL files
    for jsonl in "$DATA_DIR"/signal_log.jsonl "$DATA_DIR"/outcomes.jsonl "$DATA_DIR"/edge_ledger.json; do
        if [ -f "$jsonl" ]; then
            BASENAME=$(basename "$jsonl")
            DEST="$BACKUP_DIR/${BASENAME%.*}_${TIMESTAMP}.${BASENAME##*.}.gz"
            gzip -c "$jsonl" > "$DEST"
            echo "  BACKED UP: $BASENAME → $(basename "$DEST")"
        fi
    done
fi

# 4. Rotate: delete backups older than KEEP_DAYS
echo "--- Rotating backups (keeping last $KEEP_DAYS days) ---"
DELETED=$(find "$BACKUP_DIR" -name "*.gz" -mtime +$KEEP_DAYS -delete -print 2>/dev/null | wc -l)
echo "  Deleted $DELETED old backup(s)"

# 5. Optional S3 upload
if [ "${1:-}" = "--s3" ] && [ -n "${AWS_S3_BUCKET:-}" ]; then
    echo "--- Uploading to S3: $AWS_S3_BUCKET ---"
    for gz in "$BACKUP_DIR"/*_${TIMESTAMP}*.gz; do
        if [ -f "$gz" ]; then
            aws s3 cp "$gz" "s3://$AWS_S3_BUCKET/nzt48-backups/$(basename "$gz")" --quiet
            echo "  Uploaded: $(basename "$gz")"
        fi
    done
fi

# 6. Summary
TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "*.gz" 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
echo ""
echo "=== BACKUP COMPLETE ==="
echo "  Total backups: $TOTAL_BACKUPS"
echo "  Total size: $TOTAL_SIZE"
echo "  Location: $BACKUP_DIR"
