#!/bin/bash
# AEGIS V2 — Entrypoint: runs aegis engine + supercronic for Ouroboros
set -e

echo "Starting Ouroboros cron (supercronic)..."
supercronic /app/crontab &

echo "Starting AEGIS V2 engine..."
exec aegis --config-dir /app/config --wal-dir /app/events "$@"
