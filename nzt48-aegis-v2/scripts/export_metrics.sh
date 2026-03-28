#!/bin/bash
# P7: Export engine metrics in Prometheus textfile format.
# Run via cron: * * * * * /app/scripts/export_metrics.sh
# Writes to /app/data/metrics/aegis.prom for node_exporter textfile collector.
# Book 72B: Prometheus/Grafana observability stack.

set -euo pipefail

METRICS_DIR="/app/data/metrics"
METRICS_FILE="${METRICS_DIR}/aegis.prom"
mkdir -p "${METRICS_DIR}"

# WAL size
WAL_FILE="/app/events/current.ndjson"
WAL_BYTES=0
WAL_EVENTS=0
if [ -f "${WAL_FILE}" ]; then
    WAL_BYTES=$(stat -f%z "${WAL_FILE}" 2>/dev/null || stat -c%s "${WAL_FILE}" 2>/dev/null || echo 0)
    WAL_EVENTS=$(wc -l < "${WAL_FILE}" 2>/dev/null || echo 0)
fi

# Disk usage
DISK_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')

# Container uptime
UPTIME_SECS=$(cat /proc/uptime 2>/dev/null | awk '{print int($1)}' || echo 0)

# Dynamic weights
DW_FILE="/app/config/dynamic_weights.toml"
WIN_RATE=0
TRADE_COUNT=0
if [ -f "${DW_FILE}" ]; then
    WIN_RATE=$(grep "^win_rate" "${DW_FILE}" | head -1 | awk '{print $3}' || echo 0)
    TRADE_COUNT=$(grep "^trade_count" "${DW_FILE}" | head -1 | awk '{print $3}' || echo 0)
fi

# Write Prometheus textfile format
cat > "${METRICS_FILE}.tmp" << PROM
# HELP aegis_wal_bytes_total WAL file size in bytes
# TYPE aegis_wal_bytes_total gauge
aegis_wal_bytes_total ${WAL_BYTES}
# HELP aegis_wal_events_total WAL event count
# TYPE aegis_wal_events_total gauge
aegis_wal_events_total ${WAL_EVENTS}
# HELP aegis_disk_usage_percent Disk usage percentage
# TYPE aegis_disk_usage_percent gauge
aegis_disk_usage_percent ${DISK_PCT}
# HELP aegis_uptime_seconds Container uptime in seconds
# TYPE aegis_uptime_seconds gauge
aegis_uptime_seconds ${UPTIME_SECS}
# HELP aegis_win_rate Current win rate from dynamic_weights
# TYPE aegis_win_rate gauge
aegis_win_rate ${WIN_RATE}
# HELP aegis_trade_count Total validated trades
# TYPE aegis_trade_count gauge
aegis_trade_count ${TRADE_COUNT}
PROM

mv "${METRICS_FILE}.tmp" "${METRICS_FILE}"
