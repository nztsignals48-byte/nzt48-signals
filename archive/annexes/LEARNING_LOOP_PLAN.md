# LEARNING LOOP PLAN

**Document ID:** NZT48-ANNEX-LLP-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** DRAFT — Requires sign-off before learning loop influences live trading
**Scope:** Hardening of the outcome tracking, edge ledger, meta learner, drift detection, and defensive mode subsystems within the NZT-48 learning engine

---

## 1. OBJECTIVE

Harden the NZT-48 learning loop to institutional standards so that:
1. Every signal has a resolved outcome within a strict SLA.
2. The edge ledger provides statistically meaningful confidence intervals before influencing trading decisions.
3. Drift is detected early and triggers automated defensive responses.
4. The meta learner tracks indicator predictiveness without making unsupervised changes.
5. Clear readiness gates prevent the learning loop from influencing live sizing/confidence until sufficient data exists.

---

## 2. CURRENT STATE

### 2.1 Learning Engine Components

The learning subsystem lives in `/Users/rr/nzt48-signals/learning/` and contains 30 modules:

| Module | Purpose | Status |
|--------|---------|--------|
| `edge_ledger.py` | Per-bucket win rate, expectancy, Wilson CI | Active |
| `outcomes_engine.py` | Resolves signal outcomes (win/loss/timeout) | Active |
| `drift.py` | Feature, residual, hit-rate, regime drift detection | Active |
| `meta_learner.py` | Tracks indicator predictiveness per regime | Active |
| `learning_engine.py` | Orchestrator for learning subsystems | Active |
| `signal_logger.py` | Logs all signals to `data/signal_log.jsonl` | Active |
| `decay_detector.py` | Strategy/ticker win rate decay detection | Active |
| `guardrails.py` | Limits on self-improvement (max changes, lockout) | Active |
| `calibration.py` | Confidence calibration against actual outcomes | Active |
| `expectancy_model.py` | Expected value per trade type | Active |
| `weight_optimizer.py` | Indicator weight optimization | Active |
| `param_optimizer.py` | Parameter optimization within bounds | Active |
| `strategy_tracker.py` | Per-strategy performance tracking | Active |
| `strategy_tournament.py` | Strategy comparison and ranking | Active |
| `trade_autopsy.py` | Post-trade detailed analysis | Active |
| `attribution.py` | PnL attribution to factors | Active |
| `performance_analytics.py` | Portfolio-level analytics | Active |
| `performance_attribution.py` | Detailed return attribution | Active |
| `correlation_tracker.py` | Cross-ticker correlation monitoring | Active |
| `edge_decay_engine.py` | Edge half-life and decay modeling | Active |
| `indicator_tracker.py` | Per-indicator hit rate tracking | Active |
| `move_attribution.py` | Price move attribution to catalysts | Active |
| `pattern_tracker.py` | Chart pattern recognition tracking | Active |
| `failure_analysis.py` | Systematic failure mode analysis | Active |
| `missed_trade_journal.py` | Tracks profitable trades not taken | Active |
| `adaptive_intelligence.py` | Adaptive parameter tuning | Active |
| `execution_quality_model.py` | Slippage and fill quality tracking | Active |
| `system_iq.py` | System-wide intelligence score | Active |
| `schemas.py` | Pydantic models for all learning data | Active |

### 2.2 Current Scheduling (from `main.py`)

| Task | Schedule | Reference |
|------|----------|-----------|
| Learning state save | Every 60 minutes | `main.py:2247-2253` |
| Learning state restore | On startup | `main.py:3610-3613` |
| Outcome resolver | Every 15 minutes | `main.py:2392-2398` |
| Edge ledger rebuild | Daily at 22:30 UTC | `main.py:2411-2416` |

### 2.3 Current Configuration (from `settings.yaml` PART VII)

```yaml
learning:
  guardrailed_self_improvement:
    review_window: 40         # Last N trades
    max_changes_per_week: 3
    min_trades_before_adjust: 20
    improvement_threshold: 0.05
    failures_to_lock: 3       # 3 failures = lock 4 weeks
    risk_rules_touchable: false

  decay_detector:
    strategy_wr_disable: 0.35   # Win rate < 35% = disable
    ticker_wr_disable: 0.30     # Ticker WR < 30% = disable
    drawdown_halt: 0.12         # > 12% = halt
    consecutive_losses_halt: 6  # 6 in a row = halt session
```

### 2.4 Current Problems

1. **No outcome SLA.** Signals can remain unresolved indefinitely if the outcome resolver misses them or the ticker is illiquid.
2. **No minimum sample gates.** The edge ledger computes statistics on as few as 1 outcome. These statistics are meaningless.
3. **Drift detection exists but has no automated response.** The `drift.py` module detects drift and writes reports, but nothing acts on the reports.
4. **No readiness gates.** The learning engine could theoretically influence live sizing with 5 outcomes. This is statistically irresponsible.
5. **Meta learner has no review cadence.** Indicator weight changes happen without human oversight.
6. **Edge ledger minimum sample is 20** (in code: `MIN_SAMPLE_ACTIONABLE = 20`), but this is not enforced upstream in the signal pipeline.

---

## 3. OUTCOME TRACKING SLA

### 3.1 Resolution Requirement

**Every signal must have a resolved outcome within 24 hours of the signal's target exit time.**

| Outcome Type | Definition | Resolution Trigger |
|-------------|-----------|-------------------|
| **WIN** | Price reached Target 1 (T1) or better | Position closed at profit target |
| **LOSS** | Price hit stop loss | Position closed at stop |
| **PARTIAL_WIN** | Price reached T1 but not T2; trail stop hit | Position closed with partial profit |
| **TIMEOUT** | Neither target nor stop hit within the strategy's time window | Forced resolution at market close + 1 day |
| **CANCELLED** | Signal generated but position never opened (e.g., entry price not reached) | Auto-resolved as CANCELLED after 24h if no fill |
| **DATA_ERROR** | Resolution impossible due to missing data | Flagged for manual review; excluded from edge statistics |

### 3.2 Timeout Rules

| Strategy | Max Time Window | Timeout Action |
|----------|----------------|---------------|
| S15 (2% Daily Target) | End of trading day | If not resolved by EOD + 2 hours, resolve at closing price |
| All other strategies | 5 trading days | If not resolved after 5 trading days, resolve at day-5 close |

### 3.3 Unresolved Signal Escalation

| Time Since Signal | Action |
|-------------------|--------|
| Signal + 6 hours | Outcome resolver attempts resolution on next 15-min cycle |
| Signal + 12 hours | Log WARNING: `OUTCOME_OVERDUE: {signal_id}` |
| Signal + 24 hours | Force-resolve as TIMEOUT at last available close price |
| Signal + 24 hours | Telegram alert: `OUTCOME SLA BREACH: {signal_id} force-resolved as TIMEOUT` |
| Signal + 48 hours (still no data) | Resolve as DATA_ERROR; exclude from edge statistics; alert operator |

### 3.4 Outcome Provenance

Every resolved outcome must record:

```json
{
  "signal_id": "uuid",
  "ticker": "QQQ3.L",
  "strategy": "S15",
  "direction": "LONG",
  "entry_price": 45.20,
  "exit_price": 46.10,
  "outcome": "WIN",
  "pnl_pct": 1.99,
  "pnl_r": 1.45,
  "resolved_at": "2026-02-27T17:30:00Z",
  "resolution_method": "TARGET_HIT | STOP_HIT | TIMEOUT | CANCELLED | DATA_ERROR",
  "data_source": "polygon | ibkr | yfinance_fallback",
  "time_to_resolution_hours": 6.5,
  "sla_met": true
}
```

---

## 4. BUCKET READINESS GATES

### 4.1 Edge Ledger Bucket Definition

A bucket is defined by the composite key: `strategy_tag x regime_tag x track x time_window x liquidity_bucket`

(This matches the existing `EdgeBucketKey` in `learning/schemas.py`.)

### 4.2 Minimum Sample Requirements

Before a bucket's statistics can influence ANY live trading decision:

| Metric | Minimum Sample | Purpose |
|--------|---------------|---------|
| **Win rate** | 30 resolved outcomes in bucket | Statistical significance for binomial proportion |
| **Rolling win rate** | Computed over last 60 trades (or all trades if < 60) | Trend detection |
| **Kelly criterion** | Recalculated every 20 new outcomes | Position sizing optimization |
| **Expectancy** | 30 resolved outcomes | Expected value per trade |
| **Wilson 90% CI width** | CI width must be < 0.30 | Confidence in win rate estimate |

### 4.3 Bucket Maturity States

```
NASCENT  ──→  DEVELOPING  ──→  ACTIONABLE  ──→  CALIBRATED
(0-9)         (10-29)          (30-99)           (100+)
```

| State | Outcomes | Influence on Live Trading |
|-------|----------|--------------------------|
| **NASCENT** | 0-9 | NONE. Statistics are noise. Do not display in PDF reports. |
| **DEVELOPING** | 10-29 | NONE. Display in PDF with "DEVELOPING - not actionable" label. |
| **ACTIONABLE** | 30-99 | MAY influence confidence scoring (weight = 0.5x). Kelly sizing NOT applied yet. |
| **CALIBRATED** | 100+ | FULL influence. Kelly sizing applied. Confidence adjustments at full weight. |

### 4.4 Enforcement

The signal pipeline (`signal_engine/pipeline_runner.py` or equivalent) MUST check bucket maturity before applying any learning-derived adjustment:

```python
def apply_learning_adjustment(signal, bucket_stats):
    if bucket_stats.outcome_count < 30:
        return signal  # NO adjustment — bucket too immature
    if bucket_stats.outcome_count < 100:
        # Half-weight adjustment
        adjustment = bucket_stats.suggested_adjustment * 0.5
    else:
        # Full-weight adjustment
        adjustment = bucket_stats.suggested_adjustment
    signal.confidence += adjustment
    return signal
```

---

## 5. DRIFT DETECTION

### 5.1 Current Drift Detection (from `learning/drift.py`)

The existing `DriftDetector` class monitors four types of drift:

1. **Feature drift:** Distribution shift in rvol, atr_pct, spread_bps.
2. **Residual drift:** Predicted vs actual PnL divergence.
3. **Hit-rate drift:** Win rate drop in key buckets.
4. **Regime drift:** Current market conditions vs historical baseline.

Thresholds (from code):
- Feature drift: 30% relative change in mean = LOW severity
- Residual drift: avg |predicted - actual| > 0.25R = MEDIUM severity
- Hit-rate drift: 15pp absolute win rate drop = MEDIUM severity
- HIGH/CRITICAL severity triggers defensive mode

### 5.2 Enhanced Drift Rules

| Drift Type | Rolling Window | Threshold | Severity | Action |
|-----------|---------------|-----------|----------|--------|
| **Win rate drop** | 20 trades | Win rate < 35% | WARNING | Telegram alert; flag in PDF |
| **Win rate collapse** | 30 trades | Win rate < 25% | CRITICAL | Auto-disable strategy; enter DEFENSIVE mode |
| **Consecutive losses** | N/A | 6 consecutive losses | HIGH | Halt session for remainder of day |
| **Drawdown breach** | N/A | Drawdown > 12% from equity peak | CRITICAL | KILL SWITCH: halt all trading |
| **Feature distribution shift** | 30 days | KL divergence > 0.5 vs baseline | MEDIUM | Log; flag in PDF; operator review |
| **Regime change** | 20 days | VIX regime shift (e.g., LOW to HIGH) | INFORMATIONAL | Update regime tag; adjust indicator weights per regime profile |

### 5.3 DEFENSIVE Mode

When CRITICAL drift is detected, the system enters DEFENSIVE mode:

**DEFENSIVE mode rules:**
1. **Position sizing:** Reduce ALL position sizes by 50%.
2. **Minimum confidence:** Raise minimum actionable confidence from current setting to **80**.
3. **Trade frequency:** Maximum 1 trade per day (across all strategies).
4. **New strategy activation:** NO new strategies may be activated.
5. **Duration:** DEFENSIVE mode persists until:
   - Rolling 20-trade win rate returns above 40%, OR
   - Operator manually exits DEFENSIVE mode with documented justification, OR
   - 10 trading days pass with no new CRITICAL drift events.
6. **Telegram alert on entry:** `DEFENSIVE MODE ACTIVATED: {reason}. Position sizes halved. Max 1 trade/day.`
7. **Telegram alert on exit:** `DEFENSIVE MODE DEACTIVATED: {exit_reason}. Normal operations resumed.`

### 5.4 KILL SWITCH Integration

The existing kill switch (`data/KILL_SWITCH` file) is the ultimate circuit breaker.

| Condition | Response |
|-----------|----------|
| Drawdown > 12% | Create `data/KILL_SWITCH` file automatically |
| Kill switch file detected | ALL trading halted immediately. No signals generated. No positions opened. |
| Recovery | Operator MUST delete `data/KILL_SWITCH` manually after investigation. |

---

## 6. META LEARNER

### 6.1 Purpose

The meta learner (`learning/meta_learner.py`) tracks which indicators are most predictive in each market regime. It does NOT make automated changes. It provides data for the operator's quarterly review.

### 6.2 Indicator Tracking

For each indicator used in the signal pipeline, the meta learner records:

| Metric | Description |
|--------|-------------|
| **Hit rate** | % of times the indicator's signal direction matched the eventual outcome |
| **Regime affinity** | Which regime(s) the indicator performs best in (LOW_VOL, HIGH_VOL, TRENDING, MEAN_REVERTING) |
| **Decay rate** | How quickly the indicator's predictiveness decays after signal (1h, 4h, 1d) |
| **Correlation with outcome** | Pearson correlation between indicator value and trade PnL |

### 6.3 Quarterly Review Process

- **Frequency:** Every 3 calendar months (March 1, June 1, September 1, December 1).
- **Output:** `data/meta_learner_reviews/REVIEW_YYYY_QN.md`
- **Scope:** Review all indicator performance data accumulated over the quarter.
- **Action:** Operator may adjust indicator weights in `settings.yaml` based on meta learner data.
- **Constraint:** Maximum 3 indicator weight changes per quarter. Each change must be documented with before/after values and rationale.
- **Automation boundary:** The meta learner NEVER changes weights automatically. It RECOMMENDS. The operator DECIDES.

### 6.4 Indicator Weight Bounds

All indicator weights are bounded by the existing `settings.yaml` configuration:

```yaml
adjustable_parameters:
  confidence_weights: {default: "Varied", min: -0.50, max: 0.50, step: 0.01}
```

No weight may exceed the `[-0.50, +0.50]` range regardless of meta learner recommendation.

---

## 7. READINESS GATES

### 7.1 Gate Progression

The learning loop must pass through three readiness gates before it can influence live trading:

```
GATE 0: OBSERVING  ──→  GATE 1: CONTRIBUTING  ──→  GATE 2: AUTHORITATIVE
```

### 7.2 Gate Definitions

#### GATE 0: OBSERVING (Default State)

**Requirements to enter:** System starts here automatically.

**Learning loop behavior:**
- Signals are generated using base (non-learning-adjusted) confidence scores.
- All outcomes are tracked and recorded.
- Edge ledger is computed but NOT used for sizing or confidence.
- Drift detection runs and reports but does NOT trigger defensive mode (only alerts).
- Meta learner collects data silently.

**Duration:** Until Gate 1 criteria met.

#### GATE 1: CONTRIBUTING

**Requirements to enter (ALL must be true):**
- Total resolved outcomes across all buckets: >= 100
- System has been running for >= 3 calendar months (90 days)
- At least 5 buckets have reached ACTIONABLE status (30+ outcomes each)
- Edge ledger has been manually reviewed by operator and confirmed as showing positive expectancy in at least 2 buckets
- Operator sign-off documented in `data/learning_gate_signoffs/GATE1_SIGNOFF.md`

**Learning loop behavior:**
- Edge ledger MAY influence confidence scoring at 0.5x weight (ACTIONABLE buckets only).
- Kelly criterion MAY inform position sizing recommendations (displayed, not enforced).
- Drift detection triggers DEFENSIVE mode when CRITICAL events occur.
- Meta learner recommendations visible in PDF reports.

#### GATE 2: AUTHORITATIVE

**Requirements to enter (ALL must be true):**
- Total resolved outcomes across all buckets: >= 500
- System has been running for >= 6 calendar months (180 days)
- At least 10 buckets have reached CALIBRATED status (100+ outcomes each)
- Rolling 90-day system win rate >= 40%
- Rolling 90-day system Sharpe >= 0.5
- No CRITICAL drift events in the last 30 days
- Operator sign-off documented in `data/learning_gate_signoffs/GATE2_SIGNOFF.md`

**Learning loop behavior:**
- Edge ledger at full weight for CALIBRATED buckets.
- Kelly criterion applied to position sizing (within guardrail bounds).
- Adaptive parameter tuning active (within `max_changes_per_week: 3` guardrail).
- Full drift response including automated defensive mode.

### 7.3 Gate Regression

If at any time the system no longer meets the criteria for its current gate, it DOES NOT automatically regress. However:

- If a CRITICAL drift event occurs during GATE 2, the system enters DEFENSIVE mode but stays at GATE 2.
- If the operator determines the learning loop is degrading system performance, they may manually regress to a lower gate by documenting the decision in `data/learning_gate_signoffs/REGRESSION_{date}.md`.
- After a gate regression, the system must re-qualify for the higher gate from scratch (no shortcut).

---

## 8. DATA FLOW DIAGRAM

```
Signal Generated
       │
       ▼
Signal Logger ──────────────────────► data/signal_log.jsonl
       │
       ▼
Outcome Resolver (every 15 min)
       │
       ├── WIN/LOSS/PARTIAL ──────► data/outcomes.jsonl
       ├── TIMEOUT (24h SLA) ─────► data/outcomes.jsonl
       └── CANCELLED ─────────────► data/outcomes.jsonl
                                           │
                                           ▼
                              Edge Ledger Rebuild (daily 22:30 UTC)
                                           │
                                           ├── data/edge_ledger.json
                                           └── data/edge_weekly_delta.json
                                                      │
                                                      ▼
                                        Drift Detector (runs with edge rebuild)
                                                      │
                                                      ├── NORMAL ──► Continue
                                                      ├── WARNING ─► Telegram alert
                                                      ├── HIGH ────► Halt session
                                                      └── CRITICAL ► DEFENSIVE mode
                                                                        │
                                                                        ▼
                                                            Meta Learner (quarterly)
                                                                        │
                                                                        ▼
                                                            Operator Review
                                                                        │
                                                                        ▼
                                                            Weight Adjustments
                                                            (manual, bounded)
```

---

## 9. FAILURE MODES

| # | Failure Mode | Impact | Mitigation |
|---|-------------|--------|------------|
| FM-1 | Outcome resolver crashes silently | Unresolved signals accumulate; edge ledger stale | 24h SLA with Telegram escalation; health check on resolver process |
| FM-2 | Edge ledger rebuild fails | Stale edge data used for confidence | Check `edge_ledger.json` timestamp before using; if > 48h old, fall back to base confidence |
| FM-3 | Drift detection produces false positive | Unnecessary DEFENSIVE mode activation | DEFENSIVE mode requires CRITICAL severity (two independent confirmations); operator can override |
| FM-4 | Learning state file corrupted | Lost learning progress on restart | Hourly saves with rotation (keep last 3); validate JSON before loading |
| FM-5 | Bucket never reaches ACTIONABLE (insufficient signals for niche strategy) | Learning never influences that strategy | Acceptable; base confidence is always the safe default |
| FM-6 | Meta learner recommends destructive weight change | Strategy performance degrades after quarterly review | Max 3 changes per quarter; bounded weights; revert capability (store previous weights) |
| FM-7 | DEFENSIVE mode never exits (rolling win rate stays below 40%) | System stuck in reduced capacity indefinitely | 10-day auto-exit clause; operator manual exit with documentation |
| FM-8 | Gate 1 never reached (insufficient outcomes) | Learning loop never contributes | Acceptable; system trades on base confidence indefinitely. Investigate why outcome count is low. |
| FM-9 | Kill switch activated during backtest | Backtest halted incorrectly | Kill switch only active in LIVE and PAPER modes; backtesting mode ignores kill switch |
| FM-10 | Concurrent writes to `outcomes.jsonl` | Data corruption | File-level locking via `fcntl` or write through single-threaded outcome resolver |

---

## 10. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| Drift alert fires (WARNING or CRITICAL severity) | Review the edge ledger (`data/edge_ledger.json`) for the affected buckets. Check if a regime change explains the drift (e.g., transition from LOW_VOL to HIGH_VOL would naturally shift feature distributions). If drift is regime-driven, this may be expected -- verify the regime classifier is functioning correctly. If drift is unexplained, check data quality (stale feeds can cause apparent drift). For CRITICAL drift, verify the system has entered DEFENSIVE mode automatically. Do NOT disable drift detection -- investigate root cause first. |
| Outcome SLA breaches 24h (signals remain unresolved) | SSH to server and check the outcome resolver logs: `docker logs nzt48 --tail 100 | grep OUTCOME`. Identify which signals are stuck -- query `data/signal_log.jsonl` for signals without matching entries in `data/outcomes.jsonl`. Common causes: resolver process crashed (restart it), ticker data unavailable (check provider health), or position was never filled (should auto-resolve as CANCELLED). Force-resolve stuck signals as TIMEOUT at the last available close price. Check broker connection if fills are not being reported. |
| Readiness gate blocks learning influence (GATE 0 still active) | This is expected behaviour during the first 3+ months of operation. Verify outcome count is accumulating by checking `data/outcomes.jsonl` line count. If outcome count is growing slowly, investigate why: are signals being generated but not resolved? Are strategies producing signals at all? The system trades safely on base confidence at GATE 0 -- no urgency to advance. Continue paper trading and building outcome history. Review progress monthly against Gate 1 criteria (100 outcomes, 90 days, 5 ACTIONABLE buckets). |
| Meta learner suggests indicator weight changes | Do NOT apply changes automatically -- the meta learner RECOMMENDS only. Review the quarterly report at `data/meta_learner_reviews/REVIEW_YYYY_QN.md`. For each suggested weight change, backtest the proposed change against the last 90 days of data. Compare backtest results (win rate, expectancy, Sharpe) against current weights. Maximum 3 weight changes per quarter within the `[-0.50, +0.50]` bounds. Document before/after values and rationale in the quarterly review. If backtesting shows degradation, reject the recommendation. |
| Strategy auto-disables due to <25% win rate over 30 trades | Investigate the root cause before re-enabling. Check if market regime has changed (a strategy designed for trending markets will fail in range-bound conditions). Review the last 30 trades in the edge ledger for patterns (same ticker failing? same time of day? same regime?). If regime change is the cause, the strategy may recover when the regime returns -- leave it disabled and monitor. If the strategy is fundamentally broken (poor signal logic), it should remain disabled until redesigned. Re-enable ONLY after manual review confirms the failure cause is resolved or regime-specific, and document the decision in `data/learning_gate_signoffs/`. |

---

## 11. ACCEPTANCE TESTS

### AT-1: Outcome SLA

- [ ] Generate a signal. Wait 24 hours without any price data. Verify the signal is force-resolved as TIMEOUT.
- [ ] Generate a signal. Provide price data showing T1 hit. Verify resolution as WIN within 15 minutes.
- [ ] Generate a signal where entry is never filled. Verify resolution as CANCELLED after 24h.
- [ ] Check `time_to_resolution_hours` field is populated for all outcomes. Verify no negative values.
- [ ] Simulate overdue outcome. Verify Telegram alert is sent at 12h and 24h marks.

### AT-2: Bucket Readiness

- [ ] With 0 outcomes in a bucket, verify the edge ledger returns NASCENT status and NO adjustment is applied.
- [ ] With 15 outcomes, verify DEVELOPING status and NO adjustment is applied.
- [ ] With 35 outcomes, verify ACTIONABLE status and 0.5x weight adjustment is applied (if GATE 1+ is active).
- [ ] With 105 outcomes, verify CALIBRATED status and full-weight adjustment is applied (if GATE 2 is active).
- [ ] Verify Wilson CI is computed correctly for a known test case (e.g., 20 wins out of 30 = [0.52, 0.81] at 90% CI).

### AT-3: Drift Detection

- [ ] Inject 20 consecutive losing trades. Verify win rate < 35% triggers WARNING alert.
- [ ] Inject 30 trades with win rate < 25%. Verify CRITICAL severity triggers DEFENSIVE mode.
- [ ] Inject 6 consecutive losses. Verify session halt.
- [ ] Verify DEFENSIVE mode reduces position sizes by 50% (check position sizing module output).
- [ ] Verify DEFENSIVE mode raises min confidence to 80.
- [ ] Verify DEFENSIVE mode limits to 1 trade/day.
- [ ] After 10 trading days with no CRITICAL events, verify DEFENSIVE mode auto-exits.

### AT-4: Readiness Gates

- [ ] At system start, verify GATE 0 (OBSERVING) is active.
- [ ] With 80 total outcomes, verify GATE 0 still active (need 100).
- [ ] With 100+ outcomes, 90+ days, 5+ ACTIONABLE buckets, and signed GATE1_SIGNOFF.md, verify GATE 1 activates.
- [ ] Verify GATE 2 requires 500+ outcomes, 180+ days, 10+ CALIBRATED buckets, and signed GATE2_SIGNOFF.md.
- [ ] Verify manual gate regression works and requires re-qualification.

### AT-5: Meta Learner

- [ ] After 100 outcomes, verify meta learner produces indicator performance data.
- [ ] Verify meta learner does NOT change any weights automatically.
- [ ] Verify quarterly review template is generated at `data/meta_learner_reviews/`.

---

## 12. PROOF ARTIFACTS

| # | Artifact | Location | Purpose |
|---|----------|----------|---------|
| PA-1 | Enhanced outcome resolver | `learning/outcomes_engine.py` (updated) | 24h SLA enforcement |
| PA-2 | Bucket maturity module | `learning/edge_ledger.py` (updated) | NASCENT/DEVELOPING/ACTIONABLE/CALIBRATED states |
| PA-3 | Defensive mode controller | `learning/drift.py` (updated) or new `learning/defensive_mode.py` | Automated defensive response |
| PA-4 | Gate state manager | New `learning/readiness_gates.py` | GATE 0/1/2 state machine |
| PA-5 | Gate sign-off documents | `data/learning_gate_signoffs/` | Operator approval records |
| PA-6 | Meta learner quarterly reports | `data/meta_learner_reviews/` | Indicator performance reviews |
| PA-7 | Outcome SLA dashboard | Dashboard tab or PDF section | Resolution time tracking |
| PA-8 | Updated `settings.yaml` | `config/settings.yaml` PART VII | New learning parameters |
| PA-9 | Acceptance test suite | `tests/test_learning_loop.py` | Automated verification |
| PA-10 | Learning loop architecture diagram | This document, Section 8 | Reference architecture |

---

## 13. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Operator | | | |
| Learning System Reviewer | | | |

**This plan must be signed off before the learning loop is permitted to advance beyond GATE 0 (OBSERVING).**
