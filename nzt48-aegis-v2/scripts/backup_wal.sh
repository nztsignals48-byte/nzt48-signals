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
