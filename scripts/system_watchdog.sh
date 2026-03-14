#!/usr/bin/env bash
# ==============================================================================
# NZT-48 System Watchdog
# ==============================================================================
# Runs every 5 minutes via cron. Checks:
#   1. Container is running
#   2. API is responding
#   3. Engine heartbeat is fresh (not stale)
#   4. Engine process exists inside container
#   5. Learning engine data directory is accessible
#
# On failure: restarts the affected component and sends Telegram alert.
#
# Install:
#   chmod +x /home/ubuntu/nzt48-signals/scripts/system_watchdog.sh
#   crontab -e  →  */5 * * * * /home/ubuntu/nzt48-signals/scripts/system_watchdog.sh >> /home/ubuntu/watchdog.log 2>&1
# ==============================================================================

set -euo pipefail

CONTAINER="nzt48"
API_URL="http://localhost:8000"
LOG_TAG="[NZT48-WATCHDOG]"
MAX_HEARTBEAT_STALE_SEC=300   # 5 minutes — engine should heartbeat at least this often
MAX_ENGINE_RESTARTS=5          # after this many restarts, alert without restarting

# Telegram (read from env or .env.production)
ENV_FILE="/home/ubuntu/nzt48-signals/.env.production"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set +e
    TELEGRAM_BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
    TELEGRAM_CHAT_ID=$(grep TELEGRAM_CHAT_ID "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
    set -e
fi

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(timestamp) $LOG_TAG $*"; }

send_telegram() {
    local msg="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${msg}" \
            -d "parse_mode=Markdown" \
            --max-time 10 > /dev/null 2>&1 || true
    fi
}

log "=== Watchdog check starting ==="

# ── Check 1: Container running ─────────────────────────────────────────────────
CONTAINER_STATUS=$(docker inspect "$CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo "missing")

if [ "$CONTAINER_STATUS" != "running" ]; then
    log "ALERT: Container $CONTAINER is $CONTAINER_STATUS — restarting..."
    send_telegram "🚨 *NZT-48 WATCHDOG*: Container was $CONTAINER_STATUS. Restarting now."
    docker start "$CONTAINER" 2>/dev/null || \
        (cd /home/ubuntu/nzt48-signals && docker compose up -d nzt48 2>/dev/null) || \
        docker restart "$CONTAINER" 2>/dev/null || true
    sleep 20
    log "Container restart attempted."
fi

# ── Check 2: API responding ────────────────────────────────────────────────────
API_STATUS=$(curl -s --max-time 10 "${API_URL}/api/health" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('api','error'))" 2>/dev/null || echo "unreachable")

if [ "$API_STATUS" != "ok" ]; then
    log "ALERT: API is $API_STATUS — restarting container..."
    send_telegram "🚨 *NZT-48 WATCHDOG*: API status=$API_STATUS. Restarting container."
    docker restart "$CONTAINER" 2>/dev/null || true
    sleep 20
    log "Container restarted due to API failure."
    exit 0
fi

# ── Check 3: Engine heartbeat freshness ────────────────────────────────────────
HEALTH_JSON=$(curl -s --max-time 10 "${API_URL}/api/health" 2>/dev/null || echo '{}')
ENGINE_STATUS=$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('engine','unknown'))" 2>/dev/null || echo "unknown")
# Compute age in seconds: now() - heartbeat_unix_timestamp
HEARTBEAT_AGE_INT=$(echo "$HEALTH_JSON" | python3 -c "
import sys, json, time
d = json.load(sys.stdin)
hb = d.get('engine_last_heartbeat', 0)
if hb == 0:
    print(9999)
else:
    print(int(time.time() - float(hb)))
" 2>/dev/null || echo "9999")

if [ "$ENGINE_STATUS" = "stale" ] || [ "$HEARTBEAT_AGE_INT" -gt "$MAX_HEARTBEAT_STALE_SEC" ]; then
    log "ALERT: Engine stale (status=$ENGINE_STATUS, heartbeat_age=${HEARTBEAT_AGE_INT}s)"

    # Check if engine process is alive inside container
    ENGINE_PID=$(docker exec "$CONTAINER" sh -c 'cat /proc/*/cmdline 2>/dev/null | tr "\0" " " | grep -l "main.py" | head -1 | grep -o "[0-9]*"' 2>/dev/null || echo "")

    if [ -z "$ENGINE_PID" ]; then
        log "Engine process not found in container — supervisord should restart it"
        # supervisord has autorestart=true, so just wait 60s for it to restart
        sleep 60
        # Check again
        ENGINE_STATUS2=$(curl -s --max-time 10 "${API_URL}/api/health" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('engine','unknown'))" 2>/dev/null || echo "unknown")
        if [ "$ENGINE_STATUS2" = "stale" ]; then
            log "Engine still stale after 60s — restarting entire container"
            send_telegram "🚨 *NZT-48 WATCHDOG*: Engine stale, process dead. Restarting container."
            docker restart "$CONTAINER" 2>/dev/null || true
        else
            log "Engine recovered after supervisord restart — OK"
        fi
    else
        log "Engine process exists (PID area found) but heartbeat stale — possible deadlock"
        # Kill and let supervisord revive
        docker exec "$CONTAINER" sh -c "kill \$(cat /proc/*/cmdline 2>/dev/null | tr '\0' ' ' | grep -c 'main.py' > /dev/null; echo OK)" 2>/dev/null || true
        send_telegram "⚠️ *NZT-48 WATCHDOG*: Engine heartbeat stale (${HEARTBEAT_AGE_INT}s). Investigating."
    fi
fi

# ── Check 4: Learning data directory accessible ────────────────────────────────
OUTCOMES_CHECK=$(docker exec "$CONTAINER" sh -c 'test -d /app/data && echo "ok" || echo "fail"' 2>/dev/null || echo "fail")
if [ "$OUTCOMES_CHECK" != "ok" ]; then
    log "ALERT: /app/data not accessible inside container"
    send_telegram "🚨 *NZT-48 WATCHDOG*: Data directory inaccessible. Check volume mount."
fi

# ── Check 5: Memory usage ──────────────────────────────────────────────────────
MEM_USAGE=$(docker stats "$CONTAINER" --no-stream --format "{{.MemPerc}}" 2>/dev/null | tr -d '%' | python3 -c "import sys; v=sys.stdin.read().strip(); print(int(float(v)) if v else 0)" 2>/dev/null || echo "0")
if [ "$MEM_USAGE" -gt 85 ]; then
    log "WARNING: Container memory usage is ${MEM_USAGE}% — approaching limit"
    send_telegram "⚠️ *NZT-48 WATCHDOG*: Memory at ${MEM_USAGE}%. Monitor for OOM."
fi

log "=== Watchdog check complete — container=$CONTAINER_STATUS api=$API_STATUS engine=$ENGINE_STATUS mem=${MEM_USAGE}% ==="
