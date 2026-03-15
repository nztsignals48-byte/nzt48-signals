#!/usr/bin/env bash
# ==============================================================================
# NZT-48 Backup & Disaster Recovery Script
# Automated backup of SQLite, Redis, and configuration to S3
# ==============================================================================
set -euo pipefail

# Configuration
BACKUP_DIR="/tmp/nzt48-backups"
S3_BUCKET="${NZT48_BACKUP_BUCKET:-nzt48-backups}"
S3_PREFIX="production"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DB_PATH="/app/data/nzt48.db"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-nzt48redis}"
CONFIG_DIR="/app/config"
CREDENTIALS_DIR="/app/credentials"

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# Check prerequisites
check_prerequisites() {
    log "Checking prerequisites..."

    if ! command -v aws &> /dev/null; then
        error "AWS CLI not installed. Install: pip install awscli"
        exit 1
    fi

    if ! command -v redis-cli &> /dev/null; then
        error "redis-cli not installed. Install: apt-get install redis-tools"
        exit 1
    fi

    if ! command -v sqlite3 &> /dev/null; then
        error "sqlite3 not installed. Install: apt-get install sqlite3"
        exit 1
    fi

    # Test S3 access
    if ! aws s3 ls "s3://${S3_BUCKET}" &> /dev/null; then
        error "Cannot access S3 bucket: s3://${S3_BUCKET}"
        exit 1
    fi

    log "Prerequisites OK"
}

# Create backup directory
prepare_backup_dir() {
    log "Preparing backup directory: ${BACKUP_DIR}"
    rm -rf "${BACKUP_DIR}"
    mkdir -p "${BACKUP_DIR}"
}

# Backup SQLite database
backup_sqlite() {
    log "Backing up SQLite database..."

    if [[ ! -f "${DB_PATH}" ]]; then
        error "Database file not found: ${DB_PATH}"
        return 1
    fi

    # Create backup with SQLite's .backup command (online backup)
    sqlite3 "${DB_PATH}" ".backup '${BACKUP_DIR}/nzt48_${TIMESTAMP}.db'"

    # Compress backup
    gzip -9 "${BACKUP_DIR}/nzt48_${TIMESTAMP}.db"

    # Calculate checksum
    sha256sum "${BACKUP_DIR}/nzt48_${TIMESTAMP}.db.gz" > "${BACKUP_DIR}/nzt48_${TIMESTAMP}.db.gz.sha256"

    log "SQLite backup complete: nzt48_${TIMESTAMP}.db.gz"
}

# Backup Redis AOF/RDB
backup_redis() {
    log "Backing up Redis state..."

    # Trigger BGSAVE for RDB snapshot
    if ! redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" -a "${REDIS_PASSWORD}" BGSAVE &> /dev/null; then
        error "Failed to trigger Redis BGSAVE"
        return 1
    fi

    # Wait for BGSAVE to complete (max 30 seconds)
    local retries=30
    while [[ $retries -gt 0 ]]; do
        local status=$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" -a "${REDIS_PASSWORD}" LASTSAVE)
        sleep 1
        local new_status=$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" -a "${REDIS_PASSWORD}" LASTSAVE)
        if [[ "${new_status}" != "${status}" ]]; then
            log "Redis BGSAVE completed"
            break
        fi
        ((retries--))
    done

    if [[ $retries -eq 0 ]]; then
        error "Redis BGSAVE timeout"
        return 1
    fi

    # Copy AOF file (if exists)
    if docker exec nzt48-redis test -f /data/appendonly.aof; then
        docker cp nzt48-redis:/data/appendonly.aof "${BACKUP_DIR}/redis_aof_${TIMESTAMP}.aof"
        gzip -9 "${BACKUP_DIR}/redis_aof_${TIMESTAMP}.aof"
        sha256sum "${BACKUP_DIR}/redis_aof_${TIMESTAMP}.aof.gz" > "${BACKUP_DIR}/redis_aof_${TIMESTAMP}.aof.gz.sha256"
        log "Redis AOF backup complete"
    fi

    # Copy RDB file
    if docker exec nzt48-redis test -f /data/dump.rdb; then
        docker cp nzt48-redis:/data/dump.rdb "${BACKUP_DIR}/redis_rdb_${TIMESTAMP}.rdb"
        gzip -9 "${BACKUP_DIR}/redis_rdb_${TIMESTAMP}.rdb"
        sha256sum "${BACKUP_DIR}/redis_rdb_${TIMESTAMP}.rdb.gz" > "${BACKUP_DIR}/redis_rdb_${TIMESTAMP}.rdb.gz.sha256"
        log "Redis RDB backup complete"
    fi
}

# Backup configuration files
backup_config() {
    log "Backing up configuration files..."

    # Create config archive
    tar czf "${BACKUP_DIR}/config_${TIMESTAMP}.tar.gz" -C "$(dirname ${CONFIG_DIR})" "$(basename ${CONFIG_DIR})"
    sha256sum "${BACKUP_DIR}/config_${TIMESTAMP}.tar.gz" > "${BACKUP_DIR}/config_${TIMESTAMP}.tar.gz.sha256"

    log "Config backup complete"
}

# Backup credentials (encrypted)
backup_credentials() {
    log "Backing up credentials (encrypted)..."

    # Only backup if credentials directory exists
    if [[ -d "${CREDENTIALS_DIR}" ]]; then
        # Create encrypted archive using AWS KMS
        tar czf - -C "$(dirname ${CREDENTIALS_DIR})" "$(basename ${CREDENTIALS_DIR})" | \
            aws s3 cp - "s3://${S3_BUCKET}/${S3_PREFIX}/credentials_${TIMESTAMP}.tar.gz.enc" \
            --sse aws:kms \
            --sse-kms-key-id alias/nzt48-backup-key

        log "Credentials backup complete (encrypted)"
    else
        log "Credentials directory not found, skipping"
    fi
}

# Create backup manifest
create_manifest() {
    log "Creating backup manifest..."

    cat > "${BACKUP_DIR}/manifest_${TIMESTAMP}.json" <<EOF
{
  "timestamp": "${TIMESTAMP}",
  "date": "$(date -Iseconds)",
  "hostname": "$(hostname)",
  "version": "$(cat /app/.git_sha 2>/dev/null || echo 'unknown')",
  "files": [
    $(ls -1 "${BACKUP_DIR}"/*.gz 2>/dev/null | xargs -I {} basename {} | jq -R . | paste -sd, -)
  ],
  "checksums": {
    $(ls -1 "${BACKUP_DIR}"/*.sha256 2>/dev/null | while read f; do
        file=$(basename "$f" .sha256)
        checksum=$(cat "$f" | cut -d' ' -f1)
        echo "\"$file\": \"$checksum\","
    done | sed '$ s/,$//')
  }
}
EOF

    log "Manifest created"
}

# Upload to S3
upload_to_s3() {
    log "Uploading backups to S3..."

    # Upload all backup files
    aws s3 sync "${BACKUP_DIR}" "s3://${S3_BUCKET}/${S3_PREFIX}/backups/${TIMESTAMP}/" \
        --exclude "*" \
        --include "*.gz" \
        --include "*.sha256" \
        --include "manifest_*.json" \
        --storage-class STANDARD_IA

    # Update latest pointer
    echo "${TIMESTAMP}" | aws s3 cp - "s3://${S3_BUCKET}/${S3_PREFIX}/LATEST"

    log "Upload complete: s3://${S3_BUCKET}/${S3_PREFIX}/backups/${TIMESTAMP}/"
}

# Cleanup old backups
cleanup_old_backups() {
    log "Cleaning up backups older than ${RETENTION_DAYS} days..."

    # List and delete old backups
    aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/backups/" | \
        awk '{print $2}' | \
        while read dir; do
            # Extract timestamp from directory name (format: YYYYMMDD_HHMMSS/)
            dir_date=$(echo "$dir" | sed 's|/||' | cut -d'_' -f1)
            if [[ -n "$dir_date" ]] && [[ "$dir_date" =~ ^[0-9]{8}$ ]]; then
                cutoff_date=$(date -d "${RETENTION_DAYS} days ago" +%Y%m%d)
                if [[ "$dir_date" -lt "$cutoff_date" ]]; then
                    log "Deleting old backup: $dir"
                    aws s3 rm "s3://${S3_BUCKET}/${S3_PREFIX}/backups/${dir}" --recursive
                fi
            fi
        done

    log "Cleanup complete"
}

# Verify backup integrity
verify_backup() {
    log "Verifying backup integrity..."

    # Download and verify checksums
    local temp_verify="/tmp/nzt48-verify-$$"
    mkdir -p "${temp_verify}"

    aws s3 sync "s3://${S3_BUCKET}/${S3_PREFIX}/backups/${TIMESTAMP}/" "${temp_verify}/"

    local failed=0
    for checksum_file in "${temp_verify}"/*.sha256; do
        if [[ -f "$checksum_file" ]]; then
            pushd "${temp_verify}" > /dev/null
            if ! sha256sum -c "$(basename ${checksum_file})" &> /dev/null; then
                error "Checksum verification failed: $(basename ${checksum_file})"
                ((failed++))
            fi
            popd > /dev/null
        fi
    done

    rm -rf "${temp_verify}"

    if [[ $failed -eq 0 ]]; then
        log "Backup verification PASSED"
        return 0
    else
        error "Backup verification FAILED: ${failed} files"
        return 1
    fi
}

# Main backup workflow
main() {
    log "=== NZT-48 Backup Started ==="

    check_prerequisites
    prepare_backup_dir

    # Run backups in parallel where possible
    backup_sqlite &
    local sqlite_pid=$!

    backup_redis &
    local redis_pid=$!

    backup_config &
    local config_pid=$!

    # Wait for all backups to complete
    wait $sqlite_pid || error "SQLite backup failed"
    wait $redis_pid || error "Redis backup failed"
    wait $config_pid || error "Config backup failed"

    # Credentials backup (sequential, encrypted)
    backup_credentials

    # Create manifest
    create_manifest

    # Upload to S3
    upload_to_s3

    # Verify backup
    if ! verify_backup; then
        error "Backup verification failed - manual intervention required"
        exit 1
    fi

    # Cleanup old backups
    cleanup_old_backups

    # Cleanup local backup directory
    rm -rf "${BACKUP_DIR}"

    log "=== NZT-48 Backup Complete ==="
    log "Backup location: s3://${S3_BUCKET}/${S3_PREFIX}/backups/${TIMESTAMP}/"
}

# Run main function
main "$@"
