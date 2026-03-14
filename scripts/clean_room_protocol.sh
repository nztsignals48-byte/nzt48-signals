#!/bin/bash
# ============================================================================
# NZT-48 Clean Room Protocol — Ruthless Deprecation & Pruning (V8.0)
# ============================================================================
# Imperative 6: Codebase pruning, dead code removal, automated storage cron.
# Run manually or via cron. Safe to re-run (idempotent).
# ============================================================================

set -euo pipefail

APP_DIR="${APP_DIR:-/app}"
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$APP_DIR/artifacts}"
LOG_DIR="${DATA_DIR}"

echo "=========================================="
echo "NZT-48 Clean Room Protocol"
echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "APP_DIR: $APP_DIR"
echo "=========================================="

# ---------------------------------------------------------------------------
# Phase 1: Config Pruning — Detect orphan tickers
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 1: Config Pruning ==="

# Check for orphan tickers in settings.yaml that aren't in isa_universe.py
if [ -f "$APP_DIR/config/settings.yaml" ]; then
    echo "Checking for orphan tickers in settings.yaml..."
    ORPHANS=$(grep -oP '[A-Z0-9]{2,5}[S]\.L' "$APP_DIR/config/settings.yaml" 2>/dev/null | sort -u || true)
    if [ -n "$ORPHANS" ]; then
        echo "  WARNING: Potential orphan inverse tickers found in settings.yaml:"
        echo "  $ORPHANS"
        echo "  Verify these exist in uk_isa/isa_universe.py CORE_UNIVERSE"
    else
        echo "  OK — no orphan inverse tickers detected"
    fi
fi

# ---------------------------------------------------------------------------
# Phase 2: Dead Code Detection
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 2: Dead Code Detection ==="

# Check for deprecated datetime patterns
echo "Checking for deprecated datetime patterns..."
UTCNOW_COUNT=$(grep -rn "datetime.utcnow" --include="*.py" "$APP_DIR" 2>/dev/null | grep -v venv | grep -v __pycache__ | wc -l || echo "0")
NAIVE_NOW_COUNT=$(grep -rn "datetime\.now()" --include="*.py" "$APP_DIR" 2>/dev/null | grep -v venv | grep -v __pycache__ | grep -v "timezone\|ZoneInfo\|tz=" | wc -l || echo "0")
echo "  datetime.utcnow() occurrences: $UTCNOW_COUNT (should be 0)"
echo "  Naive datetime.now() occurrences: $NAIVE_NOW_COUNT (should be 0)"

# Check for duplicate ZoneInfo definitions (should only be in clock.py)
echo "Checking for duplicate ZoneInfo definitions..."
ZONEINFO_COUNT=$(grep -rn 'ZoneInfo("Europe/London")' --include="*.py" "$APP_DIR" 2>/dev/null | grep -v venv | grep -v __pycache__ | grep -v clock.py | wc -l || echo "0")
echo "  ZoneInfo('Europe/London') outside clock.py: $ZONEINFO_COUNT (should be 0)"

# Check for put_nowait without try/except
echo "Checking for unguarded put_nowait..."
PUT_NOWAIT=$(grep -rn "put_nowait" --include="*.py" "$APP_DIR" 2>/dev/null | grep -v venv | grep -v __pycache__ | wc -l || echo "0")
echo "  put_nowait occurrences: $PUT_NOWAIT (should be 0 after Streams migration)"

# Check for time.sleep in async context
echo "Checking for blocking time.sleep..."
TIME_SLEEP=$(grep -rn "time\.sleep" --include="*.py" "$APP_DIR" 2>/dev/null | grep -v venv | grep -v __pycache__ | grep -v "asyncio" | wc -l || echo "0")
echo "  time.sleep occurrences: $TIME_SLEEP (review — should use asyncio.sleep in async)"

# ---------------------------------------------------------------------------
# Phase 3: Filesystem Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 3: Filesystem Cleanup ==="

# Clear __pycache__ directories
PYCACHE_COUNT=$(find "$APP_DIR" -type d -name "__pycache__" 2>/dev/null | wc -l || echo "0")
echo "  Found $PYCACHE_COUNT __pycache__ directories"
if [ "$PYCACHE_COUNT" -gt 0 ]; then
    find "$APP_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "  Cleaned __pycache__ directories"
fi

# Clear .pyc files
PYC_COUNT=$(find "$APP_DIR" -name "*.pyc" -not -path "*/venv/*" 2>/dev/null | wc -l || echo "0")
echo "  Found $PYC_COUNT .pyc files"
if [ "$PYC_COUNT" -gt 0 ]; then
    find "$APP_DIR" -name "*.pyc" -not -path "*/venv/*" -delete 2>/dev/null || true
    echo "  Cleaned .pyc files"
fi

# Remove empty directories
EMPTY_COUNT=$(find "$APP_DIR" -type d -empty -not -path "*/venv/*" -not -path "*/.git/*" 2>/dev/null | wc -l || echo "0")
echo "  Found $EMPTY_COUNT empty directories"
if [ "$EMPTY_COUNT" -gt 0 ]; then
    find "$APP_DIR" -type d -empty -not -path "*/venv/*" -not -path "*/.git/*" -delete 2>/dev/null || true
    echo "  Cleaned empty directories"
fi

# ---------------------------------------------------------------------------
# Phase 4: Data Pruning
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 4: Data Pruning ==="

# Delete old JSON artifacts (>30 days)
if [ -d "$ARTIFACTS_DIR" ]; then
    OLD_JSON=$(find "$ARTIFACTS_DIR" -name "*.json" -mtime +30 2>/dev/null | wc -l || echo "0")
    echo "  JSON artifacts older than 30 days: $OLD_JSON"
    if [ "$OLD_JSON" -gt 0 ]; then
        find "$ARTIFACTS_DIR" -name "*.json" -mtime +30 -delete 2>/dev/null || true
        echo "  Deleted $OLD_JSON old JSON artifacts"
    fi
fi

# Compress old logs (>7 days)
if [ -d "$LOG_DIR" ]; then
    OLD_LOGS=$(find "$LOG_DIR" -name "*.log" -mtime +7 -not -name "*.gz" 2>/dev/null | wc -l || echo "0")
    echo "  Log files older than 7 days (uncompressed): $OLD_LOGS"
    if [ "$OLD_LOGS" -gt 0 ]; then
        find "$LOG_DIR" -name "*.log" -mtime +7 -not -name "*.gz" -exec gzip {} \; 2>/dev/null || true
        echo "  Compressed $OLD_LOGS old log files"
    fi
fi

# Prune old SQLite data (equity_intraday > 90 days)
DB_FILE="$DATA_DIR/nzt48.db"
if [ -f "$DB_FILE" ]; then
    echo "  Pruning equity_intraday rows older than 90 days..."
    sqlite3 "$DB_FILE" "DELETE FROM equity_intraday WHERE timestamp < datetime('now', '-90 days');" 2>/dev/null || true
    echo "  SQLite VACUUM..."
    sqlite3 "$DB_FILE" "VACUUM;" 2>/dev/null || true
    echo "  SQLite pruning complete"
fi

# ---------------------------------------------------------------------------
# Phase 5: Import Verification
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 5: Import Verification ==="

# Verify all .py files compile
echo "Verifying Python file compilation..."
COMPILE_ERRORS=0
while IFS= read -r -d '' pyfile; do
    if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
        echo "  COMPILE ERROR: $pyfile"
        COMPILE_ERRORS=$((COMPILE_ERRORS + 1))
    fi
done < <(find "$APP_DIR" -name "*.py" -not -path "*/venv/*" -not -path "*/__pycache__/*" -print0 2>/dev/null)

echo "  Compilation errors: $COMPILE_ERRORS"

# ---------------------------------------------------------------------------
# Phase 6: Cron Setup (automated daily pruning)
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 6: Automated Pruning Cron ==="

CRON_MARKER="# NZT-48 Clean Room Protocol"
if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    echo "  Cron job already installed — skipping"
else
    echo "  Installing daily pruning cron (02:00 UTC)..."
    (crontab -l 2>/dev/null || true; cat << CRON
$CRON_MARKER
0 2 * * * find $ARTIFACTS_DIR -name "*.json" -mtime +30 -delete 2>/dev/null
0 2 * * * find $LOG_DIR -name "*.log" -mtime +7 -not -name "*.gz" -exec gzip {} \; 2>/dev/null
0 2 * * * sqlite3 $DB_FILE "DELETE FROM equity_intraday WHERE timestamp < datetime('now', '-90 days');" 2>/dev/null
CRON
    ) | crontab - 2>/dev/null || echo "  WARNING: Could not install cron (may need root)"
    echo "  Cron job installed"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=========================================="
echo "Clean Room Protocol Complete"
echo "Finished: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=========================================="
