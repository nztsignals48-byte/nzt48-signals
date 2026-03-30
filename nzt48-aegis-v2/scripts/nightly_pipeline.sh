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

# STEP 0: Gemini core universe scan (NON-CRITICAL — ensures fresh data for config_writer)
log "STEP 0: gemini_scanner --core — fresh universe data"
if ! python3 -m python_brain.ouroboros.gemini_scanner --core >> "$LOG" 2>&1; then
    log "WARNING: gemini_scanner failed (non-critical — config_writer will use cached/stale data)"
fi
log "STEP 0: gemini_scanner DONE"

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

# STEP 7: Claude daily decisions — D-JOURNAL + D-CONFIG (Book 72, NON-CRITICAL)
log "STEP 7: claude dispatcher --daily — journal + config audit"
if ! python3 -m python_brain.claude.dispatcher --daily --telegram >> "$LOG" 2>&1; then
    log "WARNING: claude dispatcher (daily) failed (non-critical)"
fi
log "STEP 7: claude dispatcher DONE"

# STEP 8: Claude weekly decisions — D-HYPOTHESIS + D-CLUSTER + D-DECAY (Friday only, Book 72)
DOW=$(date -u +%u)  # 1=Mon, 5=Fri
if [ "$DOW" = "5" ]; then
    log "STEP 8: claude dispatcher --weekly — hypothesis + cluster + decay (Friday)"
    if ! python3 -m python_brain.claude.dispatcher --weekly --telegram >> "$LOG" 2>&1; then
        log "WARNING: claude dispatcher (weekly) failed (non-critical)"
    fi
    log "STEP 8: claude dispatcher DONE"
else
    log "STEP 8: SKIP — weekly decisions only run on Friday (today is day $DOW)"
fi

# STEP 9: Quality gate promotion check (Book 208, NON-CRITICAL)
log "STEP 9: quality_gates.py — check PAPER strategy promotions"
if [ -f /app/python_brain/validation/quality_gates.py ]; then
    python3 -m python_brain.validation.quality_gates --summary >> "$LOG" 2>&1 || true
    # Check each PAPER strategy for promotion eligibility and notify operator
    python3 -c "
import json
from python_brain.validation.quality_gates import get_lifecycle
lc = get_lifecycle()
for name, rec in lc._strategies.items():
    if rec.state != 'PAPER':
        continue
    result = lc.check_promotion(name)
    if result.get('eligible'):
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            m = result.get('metrics', {})
            send_telegram(
                f'PROMOTION ELIGIBLE: {name}\n'
                f'Days: {m.get(\"paper_days\", 0):.0f}\n'
                f'Signals: {m.get(\"paper_signals\", 0)}\n'
                f'Win Rate: {m.get(\"paper_win_rate\", 0):.1%}\n\n'
                f'Run: python3 -m python_brain.validation.quality_gates --promote-validated {name}'
            )
        except Exception:
            pass
        print(f'ELIGIBLE: {name} — {json.dumps(result[\"metrics\"])}')
    else:
        print(f'NOT YET: {name} — {result.get(\"reason\", \"\")}')
" >> "$LOG" 2>&1 || true
    log "STEP 9: quality_gates DONE"
else
    log "STEP 9: SKIP — quality_gates.py not found"
fi

# STEP 10: Escalation status check (Book 58, NON-CRITICAL)
log "STEP 10: escalation_manager.py — check pending alerts"
if [ -f /app/python_brain/alerting/escalation_manager.py ]; then
    if ! python3 -m python_brain.alerting.escalation_manager --once >> "$LOG" 2>&1; then
        log "WARNING: escalation_manager check failed (non-critical)"
    fi
    log "STEP 10: escalation_manager DONE"
else
    log "STEP 10: SKIP — escalation_manager.py not found"
fi

# STEP 11: Save Bayesian calibration (Book 209, NON-CRITICAL)
log "STEP 11: bayesian calibration snapshot"
python3 -c "
try:
    from python_brain.aggregation.bayesian_aggregator import get_aggregator
    agg = get_aggregator()
    agg.save()
    print(f'Bayesian calibration saved: {agg.to_dict()[\"n_sources\"]} sources')
except Exception as e:
    print(f'Bayesian save skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 11: bayesian calibration DONE"

# ── STEP 12: Book 119 — MI-based feature importance analysis ──
log "STEP 12: MI signal selection analysis"
python3 -c "
try:
    from python_brain.analytics.mi_signal_selector import run_mi_analysis
    report = run_mi_analysis()
    print(f'MI analysis: {report[\"status\"]}, '
          f'{report.get(\"n_outcomes\", 0)} outcomes, '
          f'top features: {report.get(\"top_5\", [])}')
except Exception as e:
    print(f'MI analysis skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 12: MI analysis DONE"

# ── STEP 13: Book 144 — Conformal calibration summary ──
log "STEP 13: Conformal calibration report"
python3 -c "
try:
    from python_brain.analytics.conformal_calibrator import get_calibrators
    cals = get_calibrators()
    s = cals.summary
    g = s.get('global', {})
    print(f'Conformal: {g.get(\"total_recorded\", 0)} outcomes, '
          f'ECE={g.get(\"calibration_error_pct\", 0):.1f}%')
    cals.save()
except Exception as e:
    print(f'Conformal skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 13: conformal calibration DONE"

log "=========================================="
log "NIGHTLY PIPELINE COMPLETE"
log "=========================================="
