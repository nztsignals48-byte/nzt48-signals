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

# ── STEP 14: Book 6 — Strategy statistical validation ──
log "STEP 14: statistical validation — live strategy assessment"
python3 -c "
try:
    from python_brain.validation.statistical_tests import run_strategy_validation
    report = run_strategy_validation()
    s = report.get('summary', {})
    print(f'Validation: {s.get(\"passed\", 0)} passed, '
          f'{s.get(\"failed\", 0)} failed, '
          f'{s.get(\"insufficient_data\", 0)} insufficient data')
except Exception as e:
    print(f'Validation skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 14: statistical validation DONE"

# ── STEP 15: Book 8 — Live metrics summary ──
log "STEP 15: live metrics summary"
python3 -c "
try:
    from python_brain.metrics.live_metrics import get_metrics_collector
    mc = get_metrics_collector()
    s = mc.summary()
    mc.save()
    print(f'Metrics: signals={s.get(\"signals_total\", 0)}, '
          f'exits={s.get(\"total_exits\", 0)}, '
          f'net_pnl={s.get(\"net_pnl\", 0):.2f}, '
          f'WR={s.get(\"win_rate_50\", 0):.1f}%')
except Exception as e:
    print(f'Metrics skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 15: live metrics DONE"

# ── STEP 16: Book 11 — Capital phase check ──
log "STEP 16: capital phase detector"
python3 -c "
try:
    from python_brain.sizing.phase_detector import run_phase_check
    result = run_phase_check()
    print(f'Phase: {result.get(\"status\", \"unknown\")}, '
          f'phase={result.get(\"phase\", {}).get(\"label\", \"?\")}')
except Exception as e:
    print(f'Phase check skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 16: phase detector DONE"

# ── STEP 17: Book 13 — Journal generation ──
log "STEP 17: daily journal generation"
python3 -c "
try:
    from python_brain.ouroboros.journal_generator import run_journal_generation
    result = run_journal_generation()
    print(f'Journal: date={result.get(\"date\", \"\")}, '
          f'trades={result.get(\"trades\", 0)}, '
          f'pnl={result.get(\"pnl\", 0):.2f}, '
          f'insights={result.get(\"insights\", 0)}')
except Exception as e:
    print(f'Journal skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 17: journal generation DONE"

# ── STEP 18: Book 14 — Alpha decay analysis ──
log "STEP 18: alpha decay analysis"
python3 -c "
try:
    from python_brain.lifecycle.alpha_decay import run_decay_analysis
    report = run_decay_analysis()
    s = report.get('summary', {})
    print(f'Decay: {s.get(\"healthy\", 0)} healthy, '
          f'{s.get(\"decaying\", 0)} decaying, '
          f'{s.get(\"kill\", 0)} kill')
    for a in report.get('alerts', []):
        print(f'  ALERT: {a}')
except Exception as e:
    print(f'Decay analysis skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 18: alpha decay DONE"

# ── STEP 19: Book 16 — Tilt detection ──
log "STEP 19: tilt detection"
python3 -c "
try:
    from python_brain.lifecycle.tilt_detector import run_tilt_analysis
    result = run_tilt_analysis()
    print(f'Tilt: score={result.get(\"score\", 0)}, '
          f'level={result.get(\"status\", \"CALM\")}, '
          f'triggers={result.get(\"triggers\", [])}')
except Exception as e:
    print(f'Tilt analysis skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 19: tilt detection DONE"

# ── STEP 20: Book 17 — Monte Carlo risk analysis ──
log "STEP 20: Monte Carlo simulation"
python3 -c "
try:
    from python_brain.monte_carlo.engine import run_monte_carlo_nightly
    report = run_monte_carlo_nightly()
    print(f'MC: status={report.get(\"status\", \"?\")}, '
          f'P(ruin)={report.get(\"bootstrap_p_ruin\", 0):.2%}, '
          f'P(SR>0)={report.get(\"bootstrap_p_sharpe_positive\", 0):.2%}')
except Exception as e:
    print(f'Monte Carlo skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 20: Monte Carlo DONE"

# ── STEP 21: Book 19 — Transaction cost analysis ──
log "STEP 21: TCA analysis"
python3 -c "
try:
    from python_brain.execution.tca_analyzer import run_tca_nightly
    report = run_tca_nightly()
    print(f'TCA: trades={report.get(\"trade_count\", 0)}, '
          f'avg_cost={report.get(\"avg_total_shortfall_bps\", 0):.1f}bps, '
          f'compliance={report.get(\"benchmark_compliance_pct\", 0):.0f}%')
except Exception as e:
    print(f'TCA skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 21: TCA DONE"

# ── STEP 22: Book 20 — Portfolio rebalance ──
log "STEP 22: portfolio rebalance"
python3 -c "
try:
    from python_brain.portfolio.portfolio_optimizer import run_portfolio_rebalance
    report = run_portfolio_rebalance()
    print(f'Portfolio: strategies={report.get(\"metrics\", {}).get(\"num_strategies\", 0)}, '
          f'max_weight={report.get(\"metrics\", {}).get(\"max_weight\", 0):.1%}, '
          f'constrained={report.get(\"constraints_satisfied\", False)}')
except Exception as e:
    print(f'Portfolio rebalance skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 22: portfolio rebalance DONE"

# ── STEP 23: Book 24 — Event calendar refresh ──
log "STEP 23: event calendar refresh"
python3 -c "
try:
    from python_brain.events.event_calendar import run_event_refresh
    report = run_event_refresh()
    print(f'Events: total={report.get(\"total_events\", 0)}, '
          f'upcoming_7d={report.get(\"upcoming_7d\", 0)}, '
          f'next_high={report.get(\"next_high_impact\", \"none\")}')
except Exception as e:
    print(f'Event calendar skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 23: event calendar DONE"

# ── STEP 24: Book 26 — Compounding velocity ──
log "STEP 24: compounding velocity"
python3 -c "
try:
    from python_brain.sizing.compounding_velocity import run_velocity_nightly
    report = run_velocity_nightly()
    print(f'Velocity: 5d={report.get(\"velocity_5d_gbp_per_day\", 0):.2f} GBP/day, '
          f'freq_ratio={report.get(\"frequency_ratio\", 0):.2f}x, '
          f'efficiency={report.get(\"cash_efficiency\", 0):.1%}')
except Exception as e:
    print(f'Velocity skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 24: velocity DONE"

# ── STEP 25: Book 27 — Leverage optimization ──
log "STEP 25: leverage optimization"
python3 -c "
try:
    from python_brain.sizing.leverage_selector import run_leverage_nightly
    report = run_leverage_nightly()
    print(f'Leverage: tickers={report.get(\"n_tickers_updated\", 0)}, '
          f'VIX={report.get(\"vix_level\", 0):.1f}')
except Exception as e:
    print(f'Leverage optimization skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 25: leverage DONE"

# ── STEP 26: Book 28 — Daily scorecard ──
log "STEP 26: daily scorecard"
python3 -c "
try:
    from python_brain.metrics.daily_scorecard import run_scorecard_nightly
    report = run_scorecard_nightly()
    print(f'Scorecard: grade={report.get(\"grade\", \"?\")}, '
          f'net_pnl={report.get(\"net_pnl\", 0):.2f}, '
          f'gates={report.get(\"gates_passed\", 0)}/{report.get(\"gates_total\", 10)}')
except Exception as e:
    print(f'Scorecard skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 26: scorecard DONE"

# ── STEP 27: Book 23 — ML entry timing check ──
log "STEP 27: ML entry timing nightly"
python3 -c "
try:
    from python_brain.ml.entry_timing.ensemble import run_ml_nightly
    run_ml_nightly()
    print('ML nightly check complete')
except Exception as e:
    print(f'ML nightly skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 27: ML nightly DONE"

log "=========================================="
log "NIGHTLY PIPELINE COMPLETE (27 steps)"
log "=========================================="
