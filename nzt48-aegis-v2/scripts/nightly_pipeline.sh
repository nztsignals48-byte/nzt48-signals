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

# ══════════════════════════════════════════════════════════════════════════
# NEW INTEGRATION STEPS (Session 22 — institutional enrichment pipeline)
# ══════════════════════════════════════════════════════════════════════════

# ── STEP 28: Exchange calendar generation (exchange_calendars) ──
log "STEP 28: exchange calendar generation"
python3 -c "
try:
    from python_brain.feeds.exchange_calendar_provider import generate_exchange_schedules
    result = generate_exchange_schedules()
    n = len(result.get('exchanges', {}))
    print(f'Exchange calendars: {n} exchanges scheduled')
except Exception as e:
    print(f'Exchange calendars skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 28: exchange calendars DONE"

# ── STEP 29: FRED macro data fetch (fredapi) ──
log "STEP 29: FRED macro data fetch"
python3 -c "
try:
    from python_brain.feeds.fred_provider import fetch_macro_data
    result = fetch_macro_data()
    n = len(result.get('latest', {}))
    print(f'FRED macro: {n} series fetched')
except Exception as e:
    print(f'FRED macro skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 29: FRED macro DONE"

# ── STEP 30: Global macro data (pandas-datareader) ──
log "STEP 30: global macro data fetch"
python3 -c "
try:
    from python_brain.feeds.global_macro_provider import fetch_global_macro
    result = fetch_global_macro()
    n = len(result.get('rates', {}))
    print(f'Global macro: {n} rates fetched')
except Exception as e:
    print(f'Global macro skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 30: global macro DONE"

# ── STEP 31: Markov regime detection (statsmodels) ──
log "STEP 31: Markov regime detection"
python3 -c "
try:
    from python_brain.regime.markov_regime import run_regime_detection
    result = run_regime_detection()
    if result:
        print(f'Regime: {result.current_regime} (p={result.regime_probability:.3f})')
    else:
        print('Regime detection: insufficient data')
except Exception as e:
    print(f'Regime detection skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 31: regime detection DONE"

# ── STEP 32: Multi-source data enrichment (TwelveData, FMP, Finnhub, etc.) ──
log "STEP 32: multi-source data enrichment"
python3 -c "
try:
    from python_brain.feeds.multi_source_aggregator import run_full_enrichment
    result = run_full_enrichment()
    ok = len(result.get('sources_available', []))
    fail = len(result.get('sources_failed', []))
    print(f'Enrichment: {ok} sources OK, {fail} failed, '
          f'{len(result.get(\"quotes\", {}))} quotes, '
          f'{len(result.get(\"earnings_calendar\", []))} earnings events')
except Exception as e:
    print(f'Enrichment skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 32: enrichment DONE"

# ── STEP 33: SEC EDGAR filing download (sec-edgar-downloader) ──
log "STEP 33: SEC EDGAR filing download"
python3 -c "
try:
    from python_brain.feeds.sec_edgar_provider import get_material_events
    events = get_material_events(days_back=7)
    print(f'SEC filings: {len(events)} material events in last 7d')
except Exception as e:
    print(f'SEC filings skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 33: SEC filings DONE"

# ── STEP 34: FinBERT sentiment scoring ──
log "STEP 34: FinBERT sentiment scoring"
python3 -c "
try:
    from python_brain.feeds.sentiment_provider import score_sec_filings
    results = score_sec_filings()
    print(f'Sentiment: {len(results)} tickers scored')
except Exception as e:
    print(f'Sentiment scoring skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 34: sentiment scoring DONE"

# ── STEP 35: SciPy parameter optimization ──
log "STEP 35: SciPy parameter optimization"
python3 -c "
try:
    from python_brain.ouroboros.scipy_optimizer import run_and_save
    result = run_and_save()
    if result:
        print(f'Optimization: converged={result.converged}, '
              f'improvement={result.improvement_pct:+.1f}%')
    else:
        print('Optimization: insufficient data')
except Exception as e:
    print(f'Optimization skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 35: optimization DONE"

# ── STEP 36: Riskfolio portfolio optimization ──
log "STEP 36: riskfolio portfolio optimization"
python3 -c "
try:
    from python_brain.portfolio.riskfolio_optimizer import run_portfolio_rebalance
    result = run_portfolio_rebalance()
    if result:
        weights = result.get('weights', {})
        top = max(weights, key=weights.get) if weights else 'N/A'
        print(f'Riskfolio: {len(weights)} strategies, '
              f'top={top} ({weights.get(top, 0):.1%})')
    else:
        print('Riskfolio: insufficient data')
except Exception as e:
    print(f'Riskfolio skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 36: riskfolio DONE"

# ── STEP 37: QuantStats tearsheet generation ──
log "STEP 37: QuantStats tearsheet generation"
python3 -c "
try:
    from python_brain.metrics.tearsheet_generator import generate_tearsheet
    import json
    pnl_path = '/app/data/strategy_pnl_history.json'
    try:
        with open(pnl_path) as f:
            data = json.load(f)
        all_returns = []
        for returns in data.values():
            if isinstance(returns, list):
                all_returns.extend(returns)
        if all_returns and len(all_returns) > 20:
            path = generate_tearsheet(all_returns, title='AEGIS V2 Nightly Tearsheet')
            if path:
                print(f'Tearsheet: {path}')
            else:
                print('Tearsheet: generation returned None')
        else:
            print(f'Tearsheet: insufficient returns ({len(all_returns)})')
    except FileNotFoundError:
        print('Tearsheet: strategy_pnl_history.json not found')
except Exception as e:
    print(f'Tearsheet skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 37: tearsheet DONE"

# ── STEP 38: SEC Insider trading scan (edgartools) ──
log "STEP 38: SEC insider trading scan"
if ! python3 -c "
from python_brain.feeds.insider_tracker import run_insider_scan
result = run_insider_scan()
if result:
    print(f'  Insider scan: {result[\"n_tickers_with_activity\"]}/{result[\"n_tickers_scanned\"]} tickers with activity')
else:
    print('  Insider scan: no data (edgartools not installed or no tickers)')
" >> "$LOG" 2>&1; then
    log "WARNING: insider scan failed (non-critical)"
fi
log "STEP 38: insider scan DONE"

# ── STEP 39: LightGBM meta-model training ──
log "STEP 39: LightGBM meta-model training"
if ! python3 -c "
from python_brain.ml.lightgbm_scorer import train_from_trades
result = train_from_trades()
if result:
    print(f'  LGB training: {result[\"n_trades\"]} trades, {result[\"n_trees\"]} trees')
else:
    print('  LGB training: skipped (insufficient data or lightgbm not installed)')
" >> "$LOG" 2>&1; then
    log "WARNING: LightGBM training failed (non-critical)"
fi
log "STEP 39: LightGBM training DONE"

# ── STEP 40: Optuna hyperparameter optimization ──
log "STEP 40: Optuna hyperparameter optimization"
if ! python3 -c "
from python_brain.ouroboros.optuna_optimizer import run_optimization
result = run_optimization(n_trials=30)
if result:
    print(f'  Optuna: best Sharpe={result[\"best_sharpe\"]:.3f}, params={result[\"best_params\"]}')
else:
    print('  Optuna: skipped (insufficient data or optuna not installed)')
" >> "$LOG" 2>&1; then
    log "WARNING: Optuna optimization failed (non-critical)"
fi
log "STEP 40: Optuna DONE"

# ── STEP 41: Evidently drift detection ──
log "STEP 41: Evidently drift detection"
if ! python3 -c "
from python_brain.monitoring.drift_detector import detect_drift
result = detect_drift()
if result:
    print(f'  Drift: severity={result[\"severity\"]}, {result[\"n_features_drifted\"]} features drifted')
else:
    print('  Drift: skipped (insufficient data or evidently not installed)')
" >> "$LOG" 2>&1; then
    log "WARNING: drift detection failed (non-critical)"
fi
log "STEP 41: drift detection DONE"

# ── STEP 42: tsfresh feature engineering ──
log "STEP 42: tsfresh feature engineering"
if ! python3 -c "
from python_brain.features.tsfresh_engineer import extract_all_tickers
print('  tsfresh: feature extraction runs on next nightly with bar data')
" >> "$LOG" 2>&1; then
    log "WARNING: tsfresh import failed (non-critical)"
fi
log "STEP 42: tsfresh DONE"

# ── STEP 43: SHAP importance report ──
log "STEP 43: SHAP importance report"
if ! python3 -c "
from python_brain.analytics.shap_explainer import generate_importance_report
print('  SHAP: importance report runs when LightGBM model is trained')
" >> "$LOG" 2>&1; then
    log "WARNING: SHAP import failed (non-critical)"
fi
log "STEP 43: SHAP DONE"

# ── STEP 44: Congressional trading scan (Quiver Quant) ──
log "STEP 44: Congressional trading scan"
if python3 -c "from python_brain.feeds.congressional_tracker import run_smart_money_scan; run_smart_money_scan()" 2>>"$LOG"; then
    log "STEP 44: Congressional DONE"
else
    log "WARNING: Congressional scan failed (non-critical)"
fi
log "STEP 44: Congressional DONE"

# ── STEP 45: Social sentiment scan (StockTwits) ──
log "STEP 45: Social sentiment scan"
if python3 -c "from python_brain.feeds.social_sentiment import run_social_scan; run_social_scan()" 2>>"$LOG"; then
    log "STEP 45: Social sentiment DONE"
else
    log "WARNING: Social sentiment scan failed (non-critical)"
fi
log "STEP 45: Social DONE"

# ── STEP 46: Unusual options flow scan ──
log "STEP 46: Options flow scan"
if python3 -c "from python_brain.feeds.options_flow import run_options_scan; run_options_scan()" 2>>"$LOG"; then
    log "STEP 46: Options flow DONE"
else
    log "WARNING: Options flow scan failed (non-critical)"
fi
log "STEP 46: Options DONE"

# ── STEP 47: Crypto Fear/Greed macro overlay ──
log "STEP 47: Crypto Fear/Greed"
if python3 -c "from python_brain.feeds.crypto_fear_greed import run_fear_greed_scan; run_fear_greed_scan()" 2>>"$LOG"; then
    log "STEP 47: Crypto Fear/Greed DONE"
else
    log "WARNING: Crypto Fear/Greed scan failed (non-critical)"
fi
log "STEP 47: Fear/Greed DONE"

# ── STEP 48: Kalman hedge ratio snapshot (Session 28) ──
log "STEP 48: Kalman hedge snapshot"
if python3 -c "from python_brain.features.kalman_hedge import save_snapshot; save_snapshot()" 2>>"$LOG"; then
    log "STEP 48: Kalman hedge snapshot DONE"
else
    log "WARNING: Kalman hedge snapshot failed (non-critical)"
fi
log "STEP 48: Kalman DONE"

# ── STEP 49: ArcticDB WAL ingestion (Phase 6.3, Session 28) ──
log "STEP 49: ArcticDB WAL ingestion"
if python3 -c "from python_brain.warehouse.arcticdb_store import ingest_today_wal; n = ingest_today_wal(); print(f'ArcticDB: {n} records ingested')" 2>>"$LOG"; then
    log "STEP 49: ArcticDB ingestion DONE"
else
    log "WARNING: ArcticDB ingestion failed (non-critical)"
fi
log "STEP 49: ArcticDB DONE"

# ── STEP 50: ArcticDB compaction (Phase 6.3, Session 29) ──
# Reclaim LMDB space after version deletes. Run weekly (Sunday) to avoid nightly latency.
DOW=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DOW" -eq 7 ]; then
    log "STEP 50: ArcticDB compaction (Sunday)"
    if python3 -c "from python_brain.warehouse.arcticdb_store import get_store; get_store().compact(); print('ArcticDB: compaction complete')" 2>>"$LOG"; then
        log "STEP 50: ArcticDB compaction DONE"
    else
        log "WARNING: ArcticDB compaction failed (non-critical)"
    fi
else
    log "STEP 50: ArcticDB compaction SKIPPED (not Sunday, DOW=$DOW)"
fi
log "STEP 50: ArcticDB compact DONE"

# ── STEP 51: Earnings boost producer (per-symbol earnings proximity scores) ──
log "STEP 51: earnings boost producer"
python3 -c "
try:
    from python_brain.feeds.earnings_boost_producer import run_earnings_boost
    result = run_earnings_boost()
    print(f'Earnings boost: {result[\"scores_produced\"]} scores '
          f'({result[\"symbols_boosted\"]} boosted, {result[\"symbols_penalized\"]} penalized), '
          f'{result[\"total_dates\"]} dates ({result[\"ibkr_dates\"]} IBKR, {result[\"yf_dates\"]} yfinance), '
          f'{result[\"duration_secs\"]}s')
except Exception as e:
    print(f'Earnings boost skipped: {e}')
" >> "$LOG" 2>&1
log "STEP 51: earnings boost DONE"

log "=========================================="
log "NIGHTLY PIPELINE COMPLETE (51 steps)"
log "=========================================="
