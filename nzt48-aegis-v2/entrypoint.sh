#!/bin/bash
# AEGIS V2 — Entrypoint: runs aegis engine + supercronic + WAL watcher
set -e

IBKR_HOST=${IBKR_HOST:-ib-gateway}
IBKR_PORT=${IBKR_PORT:-4003}

echo "Starting Ouroboros cron (supercronic)..."
supercronic /app/crontab &

# Print persistent memory summary (cumulative system knowledge)
python3 -m python_brain.ouroboros.persistent_memory 2>/dev/null || echo "No system memory yet (first boot)"

# Ensure dynamic_weights.toml is fresh before engine reads it.
# This prevents stale weights when container restarts after the 04:51 UTC cron window.
echo "Running config_writer (pre-boot refresh)..."
python3 -m python_brain.ouroboros.config_writer 2>&1 || echo "Config writer failed (non-fatal — engine will use existing file)"

echo "Starting WAL watcher (Telegram notifications)..."
python3 -m python_brain.ouroboros.wal_watcher \
    --wal-dir /app/events \
    --config-dir /app/config &

# N10a: Start kill switch Telegram listener (remote control via /kill, /pause, /resume, /status)
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    echo "Starting kill switch listener (Telegram remote control)..."
    python3 -m python_brain.ouroboros.kill_switch --listen &
else
    echo "Kill switch listener skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)"
fi

# RT4-P2: Bridge SPOF watchdog (heartbeat monitor + auto-restart)
echo "Starting bridge watchdog..."
python3 -m python_brain.ouroboros.bridge_watchdog &

# N10a: Clean up stale KILL/PAUSE files from previous runs
rm -f /app/data/KILL /app/data/PAUSE

echo "Starting AEGIS V2 engine..."
exec aegis --config-dir /app/config --wal-dir /app/events \
    --ibkr-host "$IBKR_HOST" --ibkr-port "$IBKR_PORT" "$@"
