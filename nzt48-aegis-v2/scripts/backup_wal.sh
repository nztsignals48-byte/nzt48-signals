#!/bin/bash
# P7: Daily WAL + config backup to local archive.
# Run via cron: 0 5 * * * /app/scripts/backup_wal.sh
# Book 87: S3 backup (future), for now local archive with 30-day retention.

set -euo pipefail

BACKUP_DIR="/app/data/backups"
DATE=$(date -u +%Y%m%d_%H%M%S)
ARCHIVE="${BACKUP_DIR}/aegis_backup_${DATE}.tar.gz"

mkdir -p "${BACKUP_DIR}"

# Archive WAL events + config + dynamic_weights + system_memory
tar -czf "${ARCHIVE}" \
    -C /app \
    events/ \
    config/config.toml \
    config/dynamic_weights.toml \
    data/system_memory.json \
    2>/dev/null || true

# Size check
SIZE=$(du -sh "${ARCHIVE}" 2>/dev/null | cut -f1)
echo "[Backup] ${DATE}: ${ARCHIVE} (${SIZE})"

# Retention: delete backups older than 30 days
find "${BACKUP_DIR}" -name "aegis_backup_*.tar.gz" -mtime +30 -delete 2>/dev/null || true

KEPT=$(ls -1 "${BACKUP_DIR}"/aegis_backup_*.tar.gz 2>/dev/null | wc -l)
echo "[Backup] Retained ${KEPT} backup(s)"

# Phase 7.2: S3 backup (optional) — sync archive to S3 if AWS CLI + bucket configured
S3_BUCKET="${AEGIS_BACKUP_S3_BUCKET:-}"
if [ -n "${S3_BUCKET}" ] && command -v aws &>/dev/null; then
    echo "[Backup] Uploading to s3://${S3_BUCKET}/backups/..."
    aws s3 cp "${ARCHIVE}" "s3://${S3_BUCKET}/backups/" --quiet 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "[Backup] S3 upload OK: s3://${S3_BUCKET}/backups/$(basename ${ARCHIVE})"
    else
        echo "[Backup] S3 upload FAILED (non-fatal)"
    fi
    # Also sync config and WAL metadata (not full WAL — too large)
    aws s3 sync /app/config/ "s3://${S3_BUCKET}/config/" --quiet --exclude "*.tmp" 2>/dev/null || true
    echo "[Backup] S3 config sync done"
else
    if [ -n "${S3_BUCKET}" ]; then
        echo "[Backup] WARN: AEGIS_BACKUP_S3_BUCKET set but aws CLI not found"
    fi
fi
