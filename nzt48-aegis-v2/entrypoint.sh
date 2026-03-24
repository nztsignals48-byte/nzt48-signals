#!/bin/bash
# AEGIS V2 — Entrypoint: runs aegis engine + supercronic + WAL watcher
# NOTE: no set -e — pre-boot steps are best-effort; only the engine exec is critical.

IBKR_HOST=${IBKR_HOST:-ib-gateway}
IBKR_PORT=${IBKR_PORT:-4003}

# ============================================================================
# PRE-FLIGHT DEPENDENCY CHECKS (fail-loud, not fail-silent)
# Institutional requirement: crash on boot if critical deps are misconfigured.
# ============================================================================
echo "Running pre-flight dependency checks..."
PREFLIGHT_FAIL=0

# Check critical config files exist
for f in /app/config/config.toml /app/config/contracts.toml; do
    if [ ! -f "$f" ]; then
        echo "PREFLIGHT FAIL: Missing critical config file: $f"
        PREFLIGHT_FAIL=1
    fi
done

# Check critical env vars
if [ -z "$REDIS_PASSWORD" ]; then
    echo "PREFLIGHT FAIL: REDIS_PASSWORD not set"
    PREFLIGHT_FAIL=1
fi

# Warn (non-fatal) for optional but important deps
if [ -z "$GEMINI_API_KEY" ]; then
    echo "PREFLIGHT WARN: GEMINI_API_KEY not set — Gemini universe curation will be disabled"
fi
if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "PREFLIGHT WARN: Telegram not configured — alerts/kill-switch disabled"
fi

# Check disk space (fail if <10% free)
DISK_PCT=$(df --output=pcent / 2>/dev/null | tail -1 | tr -d ' %' || echo "0")
if [ "$DISK_PCT" -gt 90 ] 2>/dev/null; then
    echo "PREFLIGHT FAIL: Disk usage ${DISK_PCT}% — need <90% for WAL writes and rebuilds"
    PREFLIGHT_FAIL=1
fi

if [ "$PREFLIGHT_FAIL" -eq 1 ]; then
    echo "PREFLIGHT: CRITICAL failures detected. Engine will NOT start."
    echo "PREFLIGHT: Fix the above issues and restart the container."
    exit 1
fi
echo "Pre-flight checks passed."

# Sprint S02: Initialize Claude cold-path directories
echo "Initializing data governance directories..."
mkdir -p /app/data/claude/{reviews,briefings,challenges,curation,rejected_reviews,anomalies,macro}
mkdir -p /app/data/curation_comparison
mkdir -p /app/data/sde_tests
mkdir -p /app/prompts

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
python3 -m python_brain.ouroboros.bridge_watchdog --monitor &

# N10a: Clean up stale KILL/PAUSE files from previous runs
rm -f /app/data/KILL /app/data/PAUSE

# Warn loudly if Gemini API key is missing (Gemini scanner crons will fail silently)
if [ -z "$GEMINI_API_KEY" ]; then
    echo "WARNING: GEMINI_API_KEY is not set. All Gemini scanner crons will fail silently."
    echo "WARNING: Set GEMINI_API_KEY in .env file and rebuild to enable Gemini universe curation."
fi

echo "Starting AEGIS V2 engine..."
exec aegis --config-dir /app/config --wal-dir /app/events \
    --ibkr-host "$IBKR_HOST" --ibkr-port "$IBKR_PORT" "$@"
