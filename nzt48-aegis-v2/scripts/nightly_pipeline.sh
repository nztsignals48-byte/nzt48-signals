#!/bin/bash
# AEGIS V2 — Sequential Nightly Pipeline (H1)
# Sprint S05: Replaces individual cron entries to prevent race conditions.
# Run via: flock -n /tmp/nightly.lock /app/scripts/nightly_pipeline.sh
set -euo pipefail

LOG="/var/log/nightly_pipeline.log"

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" | tee -a "$LOG"
}

alert_operator() {
    # Best-effort Telegram alert on critical failure
    python3 -c "
from python_brain.ouroboros.claude_helper import send_telegram
send_telegram('NIGHTLY PIPELINE FAILURE: $1')
" 2>/dev/null || true
}

cd /app

log "=========================================="
log "NIGHTLY PIPELINE START"
log "=========================================="

# STEP 1: Ouroboros nightly analysis (CRITICAL — abort on failure)
log "STEP 1: nightly_v6.py — Ouroboros expectancy analysis"
if ! python3 -m python_brain.ouroboros.nightly_v6 >> "$LOG" 2>&1; then
    log "FATAL: nightly_v6 FAILED — aborting pipeline"
    alert_operator "nightly_v6 FAILED — pipeline aborted"
    exit 1
fi
log "STEP 1: nightly_v6 DONE"

# STEP 2: Config writer (CRITICAL — abort on failure)
log "STEP 2: config_writer.py — generate dynamic_weights.toml"
if ! python3 -m python_brain.ouroboros.config_writer >> "$LOG" 2>&1; then
    log "FATAL: config_writer FAILED — aborting pipeline"
    alert_operator "config_writer FAILED — pipeline aborted"
    exit 1
fi
log "STEP 2: config_writer DONE"

# STEP 3: Win/loss delta + Google Sheets (NON-CRITICAL — continue on failure)
log "STEP 3: win_loss_delta.py — performance metrics"
if ! python3 -m python_brain.ouroboros.win_loss_delta --push-sheets >> "$LOG" 2>&1; then
    log "WARNING: win_loss_delta failed (non-critical, continuing)"
fi
log "STEP 3: win_loss_delta DONE"

# STEP 4: Claude forensic review (NON-CRITICAL — continue on failure)
log "STEP 4: claude_review.py — nightly forensic review"
if ! python3 -m python_brain.ouroboros.claude_review --send-telegram >> "$LOG" 2>&1; then
    log "WARNING: claude_review failed (trading unaffected, continuing)"
    alert_operator "claude_review failed (trading unaffected)"
fi
log "STEP 4: claude_review DONE"

# STEP 5: Ouroboros challenger (SKIP if not yet created)
log "STEP 5: ouroboros_challenger.py — parameter challenge"
if [ -f /app/python_brain/ouroboros/ouroboros_challenger.py ]; then
    if ! python3 -m python_brain.ouroboros.ouroboros_challenger --send-telegram >> "$LOG" 2>&1; then
        log "WARNING: ouroboros_challenger failed (non-critical)"
    fi
    log "STEP 5: challenger DONE"
else
    log "STEP 5: SKIP — ouroboros_challenger.py not found"
fi

# STEP 6: Approval gate (SKIP if not yet created)
log "STEP 6: approval_gate.py — governed config changes"
if [ -f /app/python_brain/ouroboros/approval_gate.py ]; then
    if ! python3 -m python_brain.ouroboros.approval_gate >> "$LOG" 2>&1; then
        log "WARNING: approval_gate failed (non-critical)"
    fi
    log "STEP 6: approval_gate DONE"
else
    log "STEP 6: SKIP — approval_gate.py not found"
fi

log "=========================================="
log "NIGHTLY PIPELINE COMPLETE"
log "=========================================="
