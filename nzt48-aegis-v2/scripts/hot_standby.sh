#!/bin/bash
# P10: Hot Standby Snapshot (Book 87)
# Creates a recovery-ready snapshot that can restore the system in <60s.
# Run: bash scripts/hot_standby.sh
# Restore: bash scripts/hot_standby.sh --restore

set -euo pipefail

SNAPSHOT_DIR="/app/data/hot_standby"
LATEST="${SNAPSHOT_DIR}/latest"

if [ "${1:-}" = "--restore" ]; then
    echo "[HotStandby] Restoring from ${LATEST}..."
    if [ ! -d "${LATEST}" ]; then
        echo "[HotStandby] ERROR: No snapshot found at ${LATEST}"
        exit 1
    fi
    cp "${LATEST}/config.toml" /app/config/config.toml
    cp "${LATEST}/dynamic_weights.toml" /app/config/dynamic_weights.toml
    cp "${LATEST}/current.ndjson" /app/events/current.ndjson 2>/dev/null || true
    echo "[HotStandby] Restored. Restart container to apply."
    exit 0
fi

# Create snapshot
mkdir -p "${SNAPSHOT_DIR}"
SNAP="${SNAPSHOT_DIR}/snap_$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "${SNAP}"

cp /app/config/config.toml "${SNAP}/"
cp /app/config/dynamic_weights.toml "${SNAP}/"
cp /app/events/current.ndjson "${SNAP}/" 2>/dev/null || true
cp /app/data/system_memory.json "${SNAP}/" 2>/dev/null || true

# Update latest symlink
rm -f "${LATEST}"
ln -s "${SNAP}" "${LATEST}"

SIZE=$(du -sh "${SNAP}" | cut -f1)
echo "[HotStandby] Snapshot: ${SNAP} (${SIZE})"

# Retain last 5 snapshots
ls -dt "${SNAPSHOT_DIR}"/snap_* 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
KEPT=$(ls -d "${SNAPSHOT_DIR}"/snap_* 2>/dev/null | wc -l)
echo "[HotStandby] Retained ${KEPT} snapshot(s)"
