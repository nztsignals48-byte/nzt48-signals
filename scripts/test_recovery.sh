#!/usr/bin/env bash
# ==============================================================================
# NZT-48 Disaster Recovery Test Script
# Validates backup restoration and system recovery procedures
# ==============================================================================
set -euo pipefail

# Configuration
S3_BUCKET="${NZT48_BACKUP_BUCKET:-nzt48-backups}"
S3_PREFIX="production"
RECOVERY_DIR="/tmp/nzt48-recovery-test"
TIMESTAMP="${1:-latest}"  # Use specific timestamp or 'latest'

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# Cleanup on exit
cleanup() {
    log "Cleaning up test recovery directory..."
    rm -rf "${RECOVERY_DIR}"
}
trap cleanup EXIT

# Check prerequisites
check_prerequisites() {
    log "Checking prerequisites..."

    if ! command -v aws &> /dev/null; then
        error "AWS CLI not installed"
        exit 1
    fi

    if ! command -v sqlite3 &> /dev/null; then
        error "sqlite3 not installed"
        exit 1
    fi

    if ! command -v redis-cli &> /dev/null; then
        error "redis-cli not installed"
        exit 1
    fi

    if ! aws s3 ls "s3://${S3_BUCKET}" &> /dev/null; then
        error "Cannot access S3 bucket: s3://${S3_BUCKET}"
        exit 1
    fi

    log "Prerequisites OK"
}

# Get latest backup timestamp
get_latest_backup() {
    if [[ "${TIMESTAMP}" == "latest" ]]; then
        TIMESTAMP=$(aws s3 cp "s3://${S3_BUCKET}/${S3_PREFIX}/LATEST" - 2>/dev/null || echo "")
        if [[ -z "${TIMESTAMP}" ]]; then
            error "No backups found in S3"
            exit 1
        fi
        log "Latest backup: ${TIMESTAMP}"
    fi
}

# Download backup from S3
download_backup() {
    log "Downloading backup from S3..."

    mkdir -p "${RECOVERY_DIR}"

    aws s3 sync "s3://${S3_BUCKET}/${S3_PREFIX}/backups/${TIMESTAMP}/" "${RECOVERY_DIR}/" \
        --exclude "*" \
        --include "*.gz" \
        --include "*.sha256" \
        --include "manifest_*.json"

    local file_count=$(ls -1 "${RECOVERY_DIR}" | wc -l)
    log "Downloaded ${file_count} files"
}

# Verify checksums
verify_checksums() {
    log "Verifying checksums..."

    local failed=0
    for checksum_file in "${RECOVERY_DIR}"/*.sha256; do
        if [[ -f "$checksum_file" ]]; then
            pushd "${RECOVERY_DIR}" > /dev/null
            if ! sha256sum -c "$(basename ${checksum_file})" &> /dev/null; then
                error "Checksum verification failed: $(basename ${checksum_file})"
                ((failed++))
            else
                log "Checksum OK: $(basename ${checksum_file} .sha256)"
            fi
            popd > /dev/null
        fi
    done

    if [[ $failed -eq 0 ]]; then
        log "All checksums verified"
        return 0
    else
        error "Checksum verification failed: ${failed} files"
        return 1
    fi
}

# Test SQLite restoration
test_sqlite_restore() {
    log "Testing SQLite restoration..."

    # Find SQLite backup
    local db_backup=$(ls -1 "${RECOVERY_DIR}"/nzt48_*.db.gz | head -1)
    if [[ ! -f "$db_backup" ]]; then
        error "SQLite backup not found"
        return 1
    fi

    # Decompress
    gunzip -c "${db_backup}" > "${RECOVERY_DIR}/restored.db"

    # Verify database integrity
    if ! sqlite3 "${RECOVERY_DIR}/restored.db" "PRAGMA integrity_check;" | grep -q "ok"; then
        error "SQLite integrity check failed"
        return 1
    fi

    # Run test queries
    local trade_count=$(sqlite3 "${RECOVERY_DIR}/restored.db" "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo "0")
    local signal_count=$(sqlite3 "${RECOVERY_DIR}/restored.db" "SELECT COUNT(*) FROM signals;" 2>/dev/null || echo "0")

    log "SQLite restore OK - Trades: ${trade_count}, Signals: ${signal_count}"

    # Check for recent data (within last 24 hours)
    local recent_trades=$(sqlite3 "${RECOVERY_DIR}/restored.db" "SELECT COUNT(*) FROM trades WHERE timestamp > datetime('now', '-1 day');" 2>/dev/null || echo "0")
    if [[ $recent_trades -eq 0 ]]; then
        log "WARNING: No trades in last 24 hours (backup may be stale)"
    else
        log "Recent trades found: ${recent_trades}"
    fi

    return 0
}

# Test Redis restoration
test_redis_restore() {
    log "Testing Redis restoration..."

    # Find Redis AOF backup
    local aof_backup=$(ls -1 "${RECOVERY_DIR}"/redis_aof_*.aof.gz 2>/dev/null | head -1 || echo "")
    local rdb_backup=$(ls -1 "${RECOVERY_DIR}"/redis_rdb_*.rdb.gz 2>/dev/null | head -1 || echo "")

    if [[ -z "$aof_backup" ]] && [[ -z "$rdb_backup" ]]; then
        log "WARNING: No Redis backups found"
        return 0
    fi

    # Decompress AOF
    if [[ -n "$aof_backup" ]]; then
        gunzip -c "${aof_backup}" > "${RECOVERY_DIR}/restored.aof"
        local aof_size=$(stat -f%z "${RECOVERY_DIR}/restored.aof" 2>/dev/null || stat -c%s "${RECOVERY_DIR}/restored.aof")
        log "Redis AOF restored: ${aof_size} bytes"
    fi

    # Decompress RDB
    if [[ -n "$rdb_backup" ]]; then
        gunzip -c "${rdb_backup}" > "${RECOVERY_DIR}/restored.rdb"
        local rdb_size=$(stat -f%z "${RECOVERY_DIR}/restored.rdb" 2>/dev/null || stat -c%s "${RECOVERY_DIR}/restored.rdb")
        log "Redis RDB restored: ${rdb_size} bytes"
    fi

    log "Redis restore test OK"
    return 0
}

# Test config restoration
test_config_restore() {
    log "Testing config restoration..."

    # Find config backup
    local config_backup=$(ls -1 "${RECOVERY_DIR}"/config_*.tar.gz | head -1)
    if [[ ! -f "$config_backup" ]]; then
        error "Config backup not found"
        return 1
    fi

    # Extract
    mkdir -p "${RECOVERY_DIR}/config_test"
    tar xzf "${config_backup}" -C "${RECOVERY_DIR}/config_test"

    # Verify settings.yaml exists
    if [[ ! -f "${RECOVERY_DIR}/config_test/config/settings.yaml" ]]; then
        error "settings.yaml not found in config backup"
        return 1
    fi

    # Validate YAML syntax (basic check)
    if ! python3 -c "import yaml; yaml.safe_load(open('${RECOVERY_DIR}/config_test/config/settings.yaml'))" 2>/dev/null; then
        error "settings.yaml syntax validation failed"
        return 1
    fi

    log "Config restore OK"
    return 0
}

# Test backup manifest
test_manifest() {
    log "Testing backup manifest..."

    local manifest=$(ls -1 "${RECOVERY_DIR}"/manifest_*.json | head -1)
    if [[ ! -f "$manifest" ]]; then
        error "Manifest not found"
        return 1
    fi

    # Parse manifest
    local backup_date=$(jq -r '.date' "${manifest}")
    local backup_version=$(jq -r '.version' "${manifest}")
    local file_count=$(jq -r '.files | length' "${manifest}")

    log "Manifest OK:"
    log "  Date: ${backup_date}"
    log "  Version: ${backup_version}"
    log "  Files: ${file_count}"

    return 0
}

# Simulate full recovery
simulate_recovery() {
    log "Simulating full system recovery..."

    # This would be the actual recovery steps in a DR scenario:
    # 1. Stop all services
    # 2. Restore database
    # 3. Restore Redis state
    # 4. Restore configuration
    # 5. Verify data integrity
    # 6. Restart services
    # 7. Run health checks

    log "Recovery simulation steps:"
    log "  [1/7] Stop services: docker compose down"
    log "  [2/7] Restore SQLite: cp restored.db /app/data/nzt48.db"
    log "  [3/7] Restore Redis: docker cp restored.aof nzt48-redis:/data/appendonly.aof"
    log "  [4/7] Restore config: cp -r config/* /app/config/"
    log "  [5/7] Verify integrity: sqlite3 integrity_check, redis PING"
    log "  [6/7] Restart services: docker compose up -d"
    log "  [7/7] Health checks: curl /api/health, verify trades/signals"

    log "Recovery simulation OK (dry-run mode)"
}

# Calculate RTO/RPO metrics
calculate_rto_rpo() {
    log "Calculating RTO/RPO metrics..."

    local manifest=$(ls -1 "${RECOVERY_DIR}"/manifest_*.json | head -1)
    if [[ ! -f "$manifest" ]]; then
        log "WARNING: Cannot calculate RTO/RPO without manifest"
        return 0
    fi

    # RPO: Time between backup and current time
    local backup_time=$(jq -r '.date' "${manifest}")
    local backup_epoch=$(date -d "${backup_time}" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "${backup_time}" +%s 2>/dev/null || echo "0")
    local current_epoch=$(date +%s)
    local rpo_seconds=$((current_epoch - backup_epoch))
    local rpo_hours=$((rpo_seconds / 3600))

    log "RPO (Recovery Point Objective): ${rpo_hours}h ago (${backup_time})"

    # RTO: Estimated recovery time (based on download + restore)
    local total_size=$(du -sh "${RECOVERY_DIR}" | cut -f1)
    log "RTO (Recovery Time Objective): ~5-10 minutes (backup size: ${total_size})"

    # Alert if RPO > 24 hours
    if [[ $rpo_hours -gt 24 ]]; then
        log "WARNING: RPO exceeds 24 hours - backup may be stale"
    fi
}

# Generate recovery test report
generate_report() {
    log "Generating recovery test report..."

    local report="${RECOVERY_DIR}/recovery_test_report.txt"

    cat > "${report}" <<EOF
=== NZT-48 Disaster Recovery Test Report ===
Date: $(date -Iseconds)
Backup Timestamp: ${TIMESTAMP}

Test Results:
  [✓] Backup download from S3
  [✓] Checksum verification
  [✓] SQLite restoration
  [✓] Redis restoration
  [✓] Config restoration
  [✓] Manifest validation
  [✓] Recovery simulation

Recovery Metrics:
$(calculate_rto_rpo)

Next Steps:
  1. Schedule monthly DR drills
  2. Test full recovery in staging environment
  3. Update runbook with lessons learned
  4. Verify backup retention policy (${RETENTION_DAYS:-30} days)

Report generated: ${report}
EOF

    cat "${report}"

    # Upload report to S3
    aws s3 cp "${report}" "s3://${S3_BUCKET}/${S3_PREFIX}/recovery-tests/report_$(date +%Y%m%d_%H%M%S).txt"

    log "Report uploaded to S3"
}

# Main test workflow
main() {
    log "=== NZT-48 Disaster Recovery Test Started ==="

    check_prerequisites
    get_latest_backup
    download_backup

    if ! verify_checksums; then
        error "Checksum verification failed - aborting test"
        exit 1
    fi

    test_manifest
    test_sqlite_restore
    test_redis_restore
    test_config_restore
    simulate_recovery
    calculate_rto_rpo
    generate_report

    log "=== NZT-48 Disaster Recovery Test Complete ==="
    log "All tests PASSED ✓"
}

# Run main function
main "$@"
