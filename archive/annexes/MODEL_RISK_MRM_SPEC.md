# MODEL RISK MANAGEMENT SPECIFICATION

**Document ID:** NZT48-ANNEX-MRM-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** BINDING -- All self-learning, adaptive, and parameter-optimization components MUST conform to this specification
**Scope:** Model governance, bounded adaptation, promotion pipeline, drift defence, and minimum sample enforcement for all learning-derived models within the NZT-48 trading system

---

## 1. PURPOSE

This specification establishes formal model governance for every component in the NZT-48 system that learns from data, adapts parameters, or optimizes weights. It exists because:

1. **Statistical irresponsibility kills accounts.** A learning engine that adjusts position sizing after 5 trades is gambling with random noise dressed up as evidence. This specification enforces minimum sample requirements that prevent premature adaptation.

2. **Uncontrolled parameter drift is invisible leverage.** When a meta-learner quietly shifts score weights by 40% over three months, the system is no longer the system that was validated. This specification bounds every adjustable parameter and triggers defensive action when drift exceeds tolerance.

3. **Models require lifecycle governance.** A model developed in research, validated on historical data, and promoted to live trading without intermediate shadow-testing is an unhedged bet on backtesting fidelity. This specification mandates a five-stage promotion pipeline with quantitative gates at each transition.

4. **Current state demands caution.** The system has executed 0 live trades. The learning engine is IDLE with no data to learn from. The risk is not what the learning engine does today -- it is what it could do tomorrow when outcomes begin accumulating, unless governance is in place before the first trade resolves.

**Governing principle:** No model output shall influence a live trading decision unless that model has passed through the full promotion pipeline, operates within its bounded parameter range, and has sufficient sample size to produce statistically meaningful results.

**Cross-references:**
- NZT48-ANNEX-LLP-001 (Learning Loop Plan) -- outcome tracking, bucket readiness, gate progression
- NZT48-ANNEX-OGV-001 (Operational Governance Plan) -- change control, run manifests, incident response
- NZT48-ANNEX-001 (Regime & Drought Spec) -- canonical regime taxonomy consumed by M5

---

## 2. MODEL INVENTORY

Every component that produces outputs used in trading decisions or that modifies system behaviour based on observed data is classified as a model and subject to this specification.

### 2.1 Model Registry

| Model ID | Name | Module(s) | Type | Risk Tier | Current Stage |
|----------|------|-----------|------|-----------|---------------|
| **M1** | Signal Scoring Model | `signal_engine/`, `config/settings.yaml` scoring section | Composite score from technical indicators (RSI, MACD, VWAP deviation, RVOL, ATR%) | HIGH | PAPER_ACTIVE |
| **M2** | Strategy Weight Allocator | `learning/meta_learner.py`, `learning/weight_optimizer.py` | Allocates compute and capital priority across strategies based on regime and edge data | HIGH | RESEARCH |
| **M3** | Edge Ledger | `learning/edge_ledger.py` | Per-bucket historical performance tracking (win rate, expectancy, Wilson CI) | MEDIUM | PAPER_ACTIVE |
| **M4** | Meta-Learner | `learning/meta_learner.py`, `learning/adaptive_intelligence.py` | Adjusts M1 and M2 parameters based on M3 data; tracks indicator predictiveness per regime | CRITICAL | RESEARCH |
| **M5** | Regime Classifier | `uk_isa/volatility_regime.py`, `config/settings.yaml` Section 7 | Classifies market into 8 macro states + 5 per-ticker volatility states per NZT48-ANNEX-001 | MEDIUM | PAPER_ACTIVE |
| **M6** | Risk Officer | `main.py` risk enforcement sections, `learning/guardrails.py` | Veto/approve/downsize decisions based on liquidity, regime, exposure, and circuit breakers | CRITICAL | PAPER_ACTIVE |
| **M7** | Drought Detector | `learning/decay_detector.py`, `learning/edge_decay_engine.py` | Identifies low-signal regimes and strategy edge decay; adjusts gate thresholds within bounds | LOW | RESEARCH |

### 2.2 Risk Tier Definitions

| Tier | Definition | Governance Requirement |
|------|-----------|----------------------|
| **CRITICAL** | Model directly influences position sizing, entry/exit decisions, or can modify other models | Full IC review for any change. Quarterly validation. Bounded knobs only. |
| **HIGH** | Model produces scores or weights consumed by CRITICAL-tier models | IC notification for changes. Monthly validation. Bounded knobs only. |
| **MEDIUM** | Model produces diagnostic or informational outputs that may indirectly influence decisions | Change documented in run manifest. Quarterly review. |
| **LOW** | Model produces advisory outputs only; no path to live trading decisions without human intervention | Change logged. Annual review. |

### 2.3 Model Dependency Graph

```
                    M6 (Risk Officer)
                    [VETO AUTHORITY]
                         |
                    vetoes/downsizes
                         |
     M4 (Meta-Learner) --+--> M1 (Signal Scoring)
         |                         |
    adjusts weights           produces scores
         |                         |
     M2 (Strategy Weight) <---+    |
         |                         |
    allocates capital         M3 (Edge Ledger)
         |                    tracks outcomes
         |                         |
     M5 (Regime Classifier)        |
    informs M1, M2, M4       M7 (Drought Detector)
                              adjusts M1 gate thresholds
```

**Constraint:** M6 (Risk Officer) has absolute veto authority over all other models. No model output can override an M6 veto or downsize decision.

---

## 3. MODEL GOVERNANCE FRAMEWORK

### 3.1 Model Ownership

Every model MUST have a designated owner responsible for its correctness, validation, and lifecycle management.

| Attribute | Requirement |
|-----------|-------------|
| **Owner** | Named individual with authority to approve changes to the model |
| **Version** | Semantic version (MAJOR.MINOR.PATCH) tracked in model metadata |
| **Training Data** | Documented: data source, date range, sample size, preprocessing steps |
| **Validation Criteria** | Quantitative thresholds the model must meet to remain in its current stage |
| **Promotion Path** | Current stage in the promotion pipeline (Section 6) and criteria for next stage |
| **Last Review Date** | Date of most recent IC/owner review |
| **Next Review Date** | Scheduled date of next mandatory review |

### 3.2 Change Control

All model changes are subject to the change control process defined in NZT48-ANNEX-OGV-001 with the following additional MRM-specific requirements:

| Change Type | Approval Required | Documentation |
|-------------|-------------------|---------------|
| **Parameter adjustment within bounded range** | Owner sign-off | Change logged in `data/model_changes/CHANGE_YYYYMMDD_HHMMSS.json` |
| **Bounded knob range modification** | IC review + sign-off | Change Request document with backtest evidence |
| **New model introduction** | IC review + sign-off + 252-day backtest | Full model specification document; enters at RESEARCH stage |
| **Model promotion (stage change)** | IC review + quantitative gate verification | Promotion report with all gate criteria evidence |
| **Model retirement** | Owner + IC notification | Retirement report; model code preserved but marked DORMANT |
| **Emergency parameter revert** | Owner (post-hoc IC notification within 24h) | Incident report per NZT48-ANNEX-OGV-001 |

### 3.3 Model Change Record Schema

Every model change MUST produce a record in the following format:

```json
{
  "change_id": "MRM-CHG-YYYYMMDD-NNN",
  "model_id": "M1",
  "model_version_before": "1.2.3",
  "model_version_after": "1.2.4",
  "change_type": "PARAM_ADJUST | BOUND_CHANGE | NEW_MODEL | PROMOTION | RETIREMENT | EMERGENCY_REVERT",
  "parameter_name": "rsi_weight",
  "value_before": 0.15,
  "value_after": 0.13,
  "justification": "Edge ledger shows RSI underperforming in HIGH_VOLATILITY regime over 120 trades",
  "evidence": {
    "backtest_sharpe": 0.62,
    "backtest_win_rate": 0.44,
    "sample_size": 120,
    "regime": "HIGH_VOLATILITY"
  },
  "approved_by": "operator",
  "approval_date": "2026-02-27T10:00:00Z",
  "ic_review_required": false,
  "ic_review_date": null
}
```

---

## 4. BOUNDED KNOBS

The following parameters are designated as **bounded knobs**: parameters that the learning engine (M4, M2, M7) is permitted to adjust within strictly defined ranges. Any value outside the stated range is a HARD BLOCK -- the system MUST reject the adjustment and log a violation.

### 4.1 Score Weight Bounds (M1 inputs adjusted by M4)

| Parameter | Baseline | Minimum | Maximum | Max Change Per Cycle | Source |
|-----------|----------|---------|---------|---------------------|--------|
| RSI weight | 0.15 | 0.12 | 0.18 | +/-0.01 per week | `settings.yaml` |
| MACD weight | 0.15 | 0.12 | 0.18 | +/-0.01 per week | `settings.yaml` |
| VWAP deviation weight | 0.20 | 0.16 | 0.24 | +/-0.01 per week | `settings.yaml` |
| RVOL weight | 0.20 | 0.16 | 0.24 | +/-0.01 per week | `settings.yaml` |
| ATR% weight | 0.15 | 0.12 | 0.18 | +/-0.01 per week | `settings.yaml` |
| Volume profile weight | 0.15 | 0.12 | 0.18 | +/-0.01 per week | `settings.yaml` |

**Aggregate constraint:** All score weights MUST sum to 1.00 +/- 0.02 after any adjustment. If normalisation would push any individual weight outside its bounds, the adjustment is BLOCKED.

**Derivation of bounds:** +/-20% of baseline value, rounded to nearest 0.01.

### 4.2 Signal Gate Thresholds (M1/M7 adjustable)

| Parameter | Baseline | Minimum | Maximum | Max Change Per Cycle | Notes |
|-----------|----------|---------|---------|---------------------|-------|
| RVOL threshold | 0.4x | 0.3x | 0.6x | +/-0.05 per week | Relative volume minimum for signal generation |
| ATR% threshold | 1.0% | 0.5% | 3.0% | +/-0.25% per week | Minimum ATR as percentage of price |
| Confidence floor | 65 | 55 | 80 | +/-3 per week | Minimum composite score to generate signal |

### 4.3 Strategy Allocation Weights (M2 adjustable)

| Parameter | Constraint | Source |
|-----------|-----------|--------|
| Per-strategy weight | Within +/-30% of regime-default allocation | `meta_learner.py` |
| Maximum single strategy weight | 0.40 (40%) absolute cap | `meta_learner.py: MAX_SINGLE_STRATEGY_WEIGHT` |
| Per-cycle weight change | +/-0.10 (10%) per update cycle | `meta_learner.py: MAX_WEIGHT_CHANGE_PER_CYCLE` |
| Maximum changes per week | 3 parameter changes across all models | `settings.yaml: max_changes_per_week` |

### 4.4 Drought Relaxation (M7 adjustable)

| Parameter | Baseline | Minimum | Maximum | Notes |
|-----------|----------|---------|---------|-------|
| Drought relaxation steps | 0 | 0 | 5 | Number of fallback levels the drought detector may engage |
| Per-step gate relaxation | -5% confidence, -0.1x RVOL | N/A | Cumulative max: -25% confidence, -0.5x RVOL | Cannot breach absolute minimums in Section 5 |

### 4.5 Enforcement Mechanism

```
Proposed adjustment
       |
       v
[Within bounded range?] --NO--> BLOCK + log BOUND_VIOLATION
       |
      YES
       |
       v
[Within per-cycle rate limit?] --NO--> BLOCK + log RATE_LIMIT_VIOLATION
       |
      YES
       |
       v
[Aggregate constraints met?] --NO--> BLOCK + log AGGREGATE_VIOLATION
(e.g., weights sum to 1.0)
       |
      YES
       |
       v
[Minimum sample met?] --NO--> BLOCK + log SAMPLE_VIOLATION
(per Section 8)
       |
      YES
       |
       v
APPLY adjustment + log change record
```

---

## 5. LOCKED PARAMETERS

The following parameters are **permanently locked**: no model, learning engine, meta-learner, or automated process may modify them under any circumstances. Changes to locked parameters require a manual code change, full IC review, and redeployment.

### 5.1 Position and Exposure Limits

| Parameter | Value | Lock Reason |
|-----------|-------|-------------|
| Maximum concurrent positions | As defined in `settings.yaml` | Capital preservation |
| Maximum risk per trade | As defined in `settings.yaml` | Single-trade blowup prevention |
| Maximum notional exposure | As defined in `settings.yaml` | Portfolio-level risk cap |
| Maximum sector concentration | As defined in `settings.yaml` | Diversification enforcement |

### 5.2 Circuit Breakers

| Parameter | Value | Lock Reason |
|-----------|-------|-------------|
| Drawdown circuit breaker (daily) | Configurable only by operator | Session-level capital protection |
| Drawdown circuit breaker (cumulative) | 12% from equity peak triggers KILL SWITCH | Account-level capital protection |
| Consecutive losses halt | 6 consecutive losses halts session | Regime detection / tilt prevention |
| Kill switch file (`data/KILL_SWITCH`) | Manual delete only to resume | Irreversible protection |

### 5.3 Leverage and Stops

| Parameter | Value | Lock Reason |
|-----------|-------|-------------|
| Leverage-once assertion | Each position sized once at entry; no post-entry leverage increase | Prevents uncontrolled exposure growth |
| Mandatory stop loss | Every position MUST have a stop loss at entry | No naked risk |
| Stop loss widening | PROHIBITED -- stops may only tighten (trail), never widen | Prevents loss amplification |
| Maximum leverage | As defined per instrument class in `settings.yaml` | Regulatory and risk compliance |

### 5.4 Risk Officer Authority

| Parameter | Value | Lock Reason |
|-----------|-------|-------------|
| M6 veto authority | Cannot be overridden by any other model | Last line of defence |
| M6 downsize authority | Cannot be countermanded by M4 or M2 | Position sizing safety |
| Risk rules touchable flag | `false` (hardcoded in `settings.yaml`) | Prevents learning engine from modifying risk parameters |

### 5.5 Data and Timing

| Parameter | Value | Lock Reason |
|-----------|-------|-------------|
| Data staleness TTLs | As defined per provider in `settings.yaml` | Prevents trading on stale data |
| Minimum R:R ratio | 1.2 | Ensures positive expectancy geometry |
| Sanity gate thresholds | Per NZT48 Sanity Gate Spec | Prevents garbage signal publication |

### 5.6 Lock Enforcement

The `learning/guardrails.py` module enforces the following invariants:

- `risk_rules_touchable: false` -- hardcoded, not configurable
- `data_health_bypass: False` -- hardcoded, NEVER overridable
- `allowed_auto_changes: []` -- empty list; all changes require manual review
- `min_sample_for_auto_change: None` -- `None` means auto-change is never permitted

Any attempt by a learning module to modify a locked parameter MUST:
1. Be rejected at the guardrails layer
2. Log a `LOCKED_PARAM_VIOLATION` event at CRITICAL severity
3. Trigger a Telegram alert to the operator
4. Increment the violation counter (3 violations in 24h triggers learning engine shutdown)

---

## 6. PROMOTION PIPELINE

Every model and every material parameter change must progress through a five-stage pipeline before influencing live trading decisions. No stage may be skipped.

### 6.1 Stage Definitions

```
RESEARCH --> PAPER_SHADOW --> PAPER_ACTIVE --> LIMITED_LIVE --> FULL_LIVE
   |              |               |               |              |
 Backtest     Shadow mode     Paper trading    10% capital    Full capital
 252 days     30 sessions     60 sessions      30 sessions    Quarterly IC
```

#### Stage 1: RESEARCH

| Attribute | Requirement |
|-----------|-------------|
| **Activity** | Model or parameter change developed and backtested on historical data |
| **Minimum duration** | N/A (backtest must cover 252 trading days of historical data) |
| **Data requirement** | 252 trading days of historical price/volume data for all target instruments |
| **Output** | Research report documenting: hypothesis, methodology, backtest results, limitations |
| **Influence on live trading** | NONE -- model outputs are not connected to any live or paper pipeline |

#### Stage 2: PAPER_SHADOW

| Attribute | Requirement |
|-----------|-------------|
| **Activity** | Model runs in shadow mode alongside the production system. Outputs are computed and logged but NOT consumed by any decision-making component |
| **Minimum duration** | 30 trading sessions |
| **Data requirement** | Real-time market data (same feed as production) |
| **Output** | Shadow log: model outputs timestamped alongside production model outputs for comparison |
| **Influence on live trading** | NONE -- outputs logged only, never consumed |

#### Stage 3: PAPER_ACTIVE

| Attribute | Requirement |
|-----------|-------------|
| **Activity** | Model used for paper trading decisions. Performance tracked against the shadow baseline and the production baseline |
| **Minimum duration** | 60 trading sessions |
| **Data requirement** | Real-time market data + paper trading execution simulation |
| **Output** | Performance report: win rate, Sharpe, max drawdown, expectancy vs shadow and production baselines |
| **Influence on live trading** | PAPER ONLY -- no real capital at risk |

#### Stage 4: LIMITED_LIVE

| Attribute | Requirement |
|-----------|-------------|
| **Activity** | Model used for live trading with 10% of allocated capital |
| **Minimum duration** | 30 trading sessions |
| **Data requirement** | Real-time market data + live execution |
| **Output** | Live performance report: slippage analysis, fill quality, win rate, Sharpe, max drawdown |
| **Influence on live trading** | LIMITED -- 10% capital only; remaining 90% uses prior production model |
| **Prerequisite** | IC sign-off documented in `data/model_promotions/M{N}_LIMITED_LIVE_SIGNOFF.md` |

#### Stage 5: FULL_LIVE

| Attribute | Requirement |
|-----------|-------------|
| **Activity** | Model used for full live trading |
| **Minimum duration** | Indefinite; quarterly IC review required |
| **Data requirement** | Continuous real-time data |
| **Output** | Quarterly performance report |
| **Influence on live trading** | FULL |
| **Prerequisite** | IC sign-off documented in `data/model_promotions/M{N}_FULL_LIVE_SIGNOFF.md` |

### 6.2 Promotion Gate Criteria

Each promotion requires ALL criteria to be met. There are no exceptions, waivers, or "good enough" approximations.

#### RESEARCH --> PAPER_SHADOW

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| Backtest Sharpe ratio | >= 0.3 | Annualised, computed over full 252-day backtest period |
| Backtest maximum drawdown | <= 15% | Peak-to-trough, computed over full backtest period |
| Backtest win rate | >= 35% | Total wins / total resolved trades |
| Research report | Complete | All sections filled: hypothesis, methodology, results, limitations |
| Code review | Passed | Model code reviewed for correctness, edge cases, data leakage |

#### PAPER_SHADOW --> PAPER_ACTIVE

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| Shadow tracking error | < 5% | Mean absolute deviation of shadow outputs vs backtest predictions |
| Anomaly count | 0 CRITICAL, <= 2 WARNING | Anomalies detected during shadow period |
| Shadow period duration | >= 30 trading sessions | Calendar count of sessions with shadow outputs |
| Data quality | No DATA_ERROR outcomes during shadow period | All outcomes resolved cleanly |

#### PAPER_ACTIVE --> LIMITED_LIVE

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| Paper trading win rate | >= 40% | Computed over all paper trades during PAPER_ACTIVE stage |
| Paper trading Sharpe | >= 0.5 | Annualised, computed over PAPER_ACTIVE period |
| Paper trading max drawdown | <= 10% | Peak-to-trough during PAPER_ACTIVE period |
| Resolved outcomes | >= 100 | Total resolved trade outcomes during PAPER_ACTIVE stage |
| IC sign-off | Documented | `data/model_promotions/M{N}_LIMITED_LIVE_SIGNOFF.md` exists and is signed |

#### LIMITED_LIVE --> FULL_LIVE

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| Consistent profitability | >= 30 sessions | Cumulative PnL positive at end of each rolling 5-session window |
| No SEV-1 incidents | 0 | No severity-1 incidents attributed to this model during LIMITED_LIVE |
| Slippage within tolerance | Mean slippage < 0.5% | Execution quality model (M6 input) confirms acceptable fill quality |
| IC sign-off | Documented | `data/model_promotions/M{N}_FULL_LIVE_SIGNOFF.md` exists and is signed |

### 6.3 Demotion Rules

| Trigger | Action |
|---------|--------|
| Model fails quarterly IC review | Demoted to PAPER_ACTIVE; must re-qualify for LIMITED_LIVE |
| SEV-1 incident attributed to model | Immediate demotion to PAPER_SHADOW; incident report required |
| Win rate drops below 30% over 60 sessions at FULL_LIVE | Demoted to PAPER_ACTIVE; root cause investigation required |
| Locked parameter violation | Immediate demotion to RESEARCH; code audit required |
| Operator manual demotion | Documented in `data/model_promotions/M{N}_DEMOTION_{date}.md`; must re-qualify from demoted stage |

### 6.4 Promotion Record Schema

```json
{
  "promotion_id": "MRM-PROMO-YYYYMMDD-NNN",
  "model_id": "M4",
  "stage_from": "PAPER_SHADOW",
  "stage_to": "PAPER_ACTIVE",
  "gate_criteria": {
    "shadow_tracking_error": {"threshold": "< 5%", "actual": "2.3%", "met": true},
    "anomaly_count": {"threshold": "<= 2 WARNING", "actual": "1 WARNING", "met": true},
    "shadow_sessions": {"threshold": ">= 30", "actual": 34, "met": true},
    "data_quality": {"threshold": "0 DATA_ERROR", "actual": 0, "met": true}
  },
  "all_gates_passed": true,
  "approved_by": "operator",
  "ic_review_date": "2026-06-15",
  "effective_date": "2026-06-16"
}
```

---

## 7. DRIFT DETECTION AND DEFENSIVE MODE

### 7.1 Bounded Knob Drift Monitoring

Every bounded knob (Section 4) is continuously monitored for drift from its baseline value. Drift is measured as the percentage change from baseline in a rolling 20-session window.

| Drift Level | Threshold | Action |
|-------------|-----------|--------|
| **NOMINAL** | Knob within 10% of baseline | No action. Normal operation. |
| **ELEVATED** | Knob between 10% and 15% of baseline | Log WARNING. Include in weekly IC memo. |
| **TRIGGERED** | Knob exceeds 15% of baseline | ENTER DEFENSIVE MODE. Revert ALL learning adjustments. Alert operator. |
| **CATASTROPHIC** | Any knob at boundary (max or min of allowed range) | IMMEDIATE DEFENSIVE MODE. All learning adjustments reverted. Telegram CRITICAL alert. Incident logged. |

### 7.2 Defensive Mode Definition

When defensive mode is triggered by knob drift:

**Immediate actions (within 60 seconds of trigger):**

1. **Revert all bounded knobs** to their baseline default values as defined in Section 4.
2. **Freeze the learning engine.** No model (M1-M7) may make any parameter adjustment while defensive mode is active.
3. **Log the incident.** Write a `DEFENSIVE_MODE_ENTRY` record to `data/drift_reports.jsonl` with:
   - Trigger reason (which knob, what value, what threshold breached)
   - All knob values at time of trigger
   - All knob baseline values
   - Timestamp
4. **Alert the operator.** Telegram message: `MRM DEFENSIVE MODE: {knob_name} drifted {X}% from baseline. All learning reverted. Manual IC review required.`

**Sustained restrictions during defensive mode:**

- All position sizing uses base (non-learning-adjusted) parameters
- All signal scoring uses base (non-learning-adjusted) weights
- Strategy allocation uses regime-default weights (no meta-learner influence)
- Drought detector relaxation steps reset to 0

**Defensive mode exit criteria (ALL must be true):**

1. Manual IC review completed and documented in `data/defensive_mode/EXIT_REVIEW_{date}.md`
2. Root cause of drift identified and documented
3. Corrective action taken (parameter bound tightened, learning rate reduced, or model demoted)
4. Operator explicitly approves re-enabling learning with documented justification

**There is no automatic exit from MRM defensive mode.** Unlike the operational defensive mode defined in NZT48-ANNEX-LLP-001 (which has a 10-day auto-exit), MRM defensive mode requires explicit human approval because the trigger indicates a potential model governance failure, not merely adverse market conditions.

### 7.3 Cross-Model Drift Correlation

When two or more models exhibit ELEVATED or TRIGGERED drift simultaneously:

| Condition | Action |
|-----------|--------|
| 2 models at ELEVATED | Escalate to TRIGGERED for all drifting models |
| Any model at TRIGGERED + another at ELEVATED | Escalate all to CATASTROPHIC |
| M4 (Meta-Learner) at any drift level | Automatically escalate one level (Meta-Learner drift compounds through the dependency graph) |

### 7.4 Drift Monitoring Schedule

| Check | Frequency | Module |
|-------|-----------|--------|
| Bounded knob position vs baseline | Every edge ledger rebuild (daily 22:30 UTC) | `learning/drift.py` |
| Cross-model drift correlation | Every edge ledger rebuild (daily 22:30 UTC) | `learning/drift.py` |
| Catastrophic boundary check | Every parameter adjustment attempt (real-time) | `learning/guardrails.py` |

---

## 8. MINIMUM SAMPLE REQUIREMENTS

No learning-derived output shall influence any trading decision until the underlying data meets the minimum sample requirements specified below. These requirements are non-negotiable and cannot be relaxed by any model, operator override, or configuration change.

### 8.1 Per-Component Minimums

| Component | Minimum Sample | What Counts as a Sample | Enforcement Point |
|-----------|---------------|------------------------|-------------------|
| **Edge Ledger (M3)** per bucket | 20 resolved trades | WIN, LOSS, PARTIAL_WIN, TIMEOUT outcomes. CANCELLED and DATA_ERROR excluded. | `edge_ledger.py: MIN_SAMPLE_ACTIONABLE = 20` |
| **Meta-Learner (M4)** global activation | 100 resolved trades total (across all strategies and buckets) | Same as Edge Ledger | Gate check before any M4 output is consumed |
| **Drift Detection baselines** | 50 resolved trades | Same as Edge Ledger | Drift baselines computed but flagged as PRELIMINARY until 50 trades; no DEFENSIVE mode trigger from preliminary baselines |
| **Regime-specific learning** | 30 trades per regime | Trades tagged with regime at entry time | Per-regime bucket in edge ledger |
| **Parameter Optimizer** per parameter band | 50 trades per band | Trades that exercised the specific parameter value | `param_optimizer.py: MIN_SAMPLES = 50` |
| **Strategy tournament** per strategy | 30 resolved trades per strategy | Same as Edge Ledger, filtered by strategy tag | `learning/strategy_tournament.py` |

### 8.2 Sample Sufficiency States

Aligning with NZT48-ANNEX-LLP-001 Section 4.3:

| State | Outcome Count | Influence Permitted |
|-------|---------------|-------------------|
| **NASCENT** | 0-9 | NONE. Statistics are noise. Not displayed in any report. |
| **DEVELOPING** | 10-29 | NONE. Displayed in reports with "DEVELOPING -- NOT ACTIONABLE" label. |
| **ACTIONABLE** | 30-99 | MAY influence confidence scoring at 0.5x weight. Kelly sizing NOT applied. |
| **CALIBRATED** | 100+ | FULL influence. Kelly sizing applied. Confidence adjustments at full weight. |

### 8.3 Sample Clock Reset

When a model is demoted (Section 6.3), its sample clock is NOT reset. Historical outcomes remain valid for the model's statistics. However:

- When a model's parameters are materially changed (any bounded knob moved by >10%), the sample clock for the AFFECTED PARAMETER resets to zero. The model must re-accumulate the minimum sample for that parameter before the new value influences decisions.
- When a model is retired and replaced by a new model, the new model starts at NASCENT regardless of the predecessor's sample count.

---

## 9. REVIEW CADENCE

### 9.1 Weekly IC Memo

**Due:** Every Monday by 09:00 UTC (covering the prior trading week).

**Location:** `data/ic_memos/IC_MEMO_YYYYMMDD.md`

**Required contents:**

| Section | Content |
|---------|---------|
| Learning Engine State | Current gate (OBSERVING / CONTRIBUTING / AUTHORITATIVE per LLP-001), total resolved outcomes, outcome delta this week |
| Model Stage Summary | Current promotion stage for each model M1-M7 |
| Bounded Knob Positions | Table of all bounded knobs: current value, baseline, % deviation, drift status |
| Drift Alerts | Any ELEVATED, TRIGGERED, or CATASTROPHIC drift events during the week |
| Parameter Changes | Any bounded knob adjustments made during the week, with change records |
| Violations | Any BOUND_VIOLATION, RATE_LIMIT_VIOLATION, AGGREGATE_VIOLATION, SAMPLE_VIOLATION, or LOCKED_PARAM_VIOLATION events |
| Defensive Mode Status | Whether defensive mode is active; if so, duration and exit criteria progress |
| Action Items | Operator actions required before next review |

### 9.2 Monthly Model Performance Review

**Due:** First Monday of each calendar month.

**Location:** `data/model_reviews/MODEL_REVIEW_YYYY_MM.md`

**Required contents:**

| Section | Content |
|---------|---------|
| Per-Model Performance | Win rate, Sharpe, expectancy, max drawdown for each active model vs its baseline |
| Promotion Pipeline Status | Which models are candidates for promotion; gate criteria progress |
| Edge Decay Analysis | Any strategies or tickers showing statistically significant edge decay (per M7) |
| Regime Distribution | Percentage of trading sessions in each regime state; comparison to historical distribution |
| Learning Engine Contribution | Quantification of learning engine's impact: how much did learning-adjusted scores differ from base scores, and did the adjustment improve or degrade outcomes? |

### 9.3 Quarterly IC Review

**Due:** First week of March, June, September, December.

**Location:** `data/ic_reviews/IC_REVIEW_YYYY_QN.md`

**Required contents:**

| Section | Content |
|---------|---------|
| Model Promotions & Demotions | All stage changes during the quarter with gate evidence |
| Parameter Bound Review | Are current bounded ranges appropriate? Any bounds that should be tightened or relaxed? |
| Meta-Learner Assessment | Review of M4 indicator predictiveness data; approve/reject recommended weight changes (max 3 per quarter per LLP-001) |
| Defensive Mode Incidents | Full review of any defensive mode activations; root cause analysis |
| Sample Sufficiency Progress | Bucket maturity progression; projection for when key buckets will reach ACTIONABLE/CALIBRATED |
| Risk Tier Reassessment | Should any model's risk tier change based on observed behaviour? |
| Sign-off | IC approval signature with date |

---

## 10. ACCEPTANCE TESTS

The following acceptance tests validate that the MRM governance framework is correctly implemented and enforced. All tests MUST pass before the learning engine is permitted to advance beyond GATE 0 (OBSERVING).

### MRM-T01: Locked Parameter Protection

**Objective:** Verify that the meta-learner cannot modify locked parameters.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Invoke M4 with a directive to adjust `max_concurrent_positions` from 3 to 5 | Adjustment BLOCKED by guardrails |
| 2 | Verify `LOCKED_PARAM_VIOLATION` event logged at CRITICAL severity | Event present in `data/model_changes/` |
| 3 | Verify Telegram alert sent | Alert received with violation details |
| 4 | Verify position limit unchanged | `settings.yaml` value unchanged; runtime value unchanged |

**Verdict:** PASS if all steps produce expected results. FAIL if any locked parameter is modified.

### MRM-T02: Drift-Triggered Defensive Mode

**Objective:** Verify that a bounded knob drifting >15% from baseline triggers defensive mode.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Set RSI weight to 0.15 (baseline) | Weight confirmed at baseline |
| 2 | Simulate learning engine adjusting RSI weight to 0.127 (15.3% below baseline) over a 20-session window | Drift detection fires |
| 3 | Verify DEFENSIVE mode activated | `data/drift_reports.jsonl` contains `DEFENSIVE_MODE_ENTRY` record |
| 4 | Verify ALL bounded knobs reverted to baseline | All knobs at baseline values |
| 5 | Verify learning engine frozen | No parameter adjustments accepted until manual exit |
| 6 | Verify Telegram CRITICAL alert sent | Alert received with drift details |
| 7 | Verify revert completed within 60 seconds of trigger | Timestamp delta < 60s |

**Verdict:** PASS if defensive mode activates, all knobs revert within 60s, and alerts fire. FAIL otherwise.

### MRM-T03: Insufficient Sample Blocking

**Objective:** Verify that the learning engine is blocked from activation with fewer than 100 resolved trades.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Populate `data/outcomes.jsonl` with exactly 50 resolved outcomes | Outcomes loaded |
| 2 | Attempt to activate M4 (meta-learner) | Activation BLOCKED with `SAMPLE_VIOLATION` |
| 3 | Verify M4 state remains RESEARCH | No stage change recorded |
| 4 | Verify no M4-derived adjustments applied to M1 or M2 | Score weights and strategy allocations unchanged from baseline |
| 5 | Add 50 more outcomes (total 100) and retry M4 activation | Activation permitted (subject to other gate criteria) |

**Verdict:** PASS if M4 is blocked at 50 trades and unblocked at 100. FAIL if M4 activates with insufficient sample.

### MRM-T04: Promotion Without Backtest Blocked

**Objective:** Verify that model promotion from RESEARCH to PAPER_SHADOW is blocked without a qualifying backtest.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Create a new model (M8) at RESEARCH stage with no backtest data | Model registered at RESEARCH |
| 2 | Attempt promotion to PAPER_SHADOW | Promotion BLOCKED: "Backtest data missing" |
| 3 | Provide backtest with Sharpe 0.2 (below 0.3 threshold) | Promotion BLOCKED: "Backtest Sharpe below threshold (0.2 < 0.3)" |
| 4 | Provide backtest with Sharpe 0.4 but max DD 18% (above 15% threshold) | Promotion BLOCKED: "Backtest max drawdown above threshold (18% > 15%)" |
| 5 | Provide qualifying backtest (Sharpe 0.4, DD 12%, WR 38%) | Promotion APPROVED |

**Verdict:** PASS if all inadequate backtests are rejected and only the qualifying backtest permits promotion. FAIL if any non-qualifying backtest is accepted.

### MRM-T05: Bounded Knobs Within Range After Paper Period

**Objective:** Verify that all bounded knobs remain within their defined ranges after 30 paper trading sessions with the learning engine active.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Run paper trading for 30 sessions with learning engine at GATE 1 (CONTRIBUTING) | 30 sessions completed |
| 2 | Extract all bounded knob values | Values retrieved |
| 3 | For each knob, verify: `minimum <= current_value <= maximum` | All knobs within bounds |
| 4 | For each knob, verify: change from baseline <= per-cycle rate limit x number of cycles | All knobs within rate limits |
| 5 | Verify aggregate constraints (e.g., score weights sum to 1.0 +/- 0.02) | Aggregate constraints met |

**Verdict:** PASS if all knobs within bounds and all constraints met. FAIL if any knob is out of range.

### MRM-T06: Weekly IC Memo Generation

**Objective:** Verify that the weekly IC memo is generated with all required fields.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Trigger weekly IC memo generation | Memo created at `data/ic_memos/IC_MEMO_YYYYMMDD.md` |
| 2 | Verify Learning Engine State section present | Section exists with gate status, outcome count, delta |
| 3 | Verify Model Stage Summary section present | Section exists with M1-M7 stages |
| 4 | Verify Bounded Knob Positions table present | Table exists with all knobs, values, baselines, deviations |
| 5 | Verify Drift Alerts section present | Section exists (may be empty if no alerts) |
| 6 | Verify Parameter Changes section present | Section exists (may be empty if no changes) |
| 7 | Verify Violations section present | Section exists (may be empty if no violations) |
| 8 | Verify Defensive Mode Status section present | Section exists with current status |
| 9 | Verify Action Items section present | Section exists |

**Verdict:** PASS if all 8 required sections are present and populated. FAIL if any section is missing.

### MRM-T07: Defensive Mode Revert Timing

**Objective:** Verify that defensive mode reverts all parameters to baseline within 60 seconds.

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Set multiple bounded knobs to non-baseline values (within allowed ranges) | Knobs at adjusted values |
| 2 | Trigger defensive mode (simulate CATASTROPHIC drift) | Defensive mode entry timestamp T1 recorded |
| 3 | Monitor all bounded knobs | All knobs return to baseline |
| 4 | Record time when last knob reaches baseline (T2) | T2 recorded |
| 5 | Verify T2 - T1 < 60 seconds | Delta < 60s |
| 6 | Verify learning engine is frozen (attempt adjustment) | Adjustment BLOCKED |

**Verdict:** PASS if all parameters revert to baseline within 60 seconds and learning engine is frozen. FAIL if revert takes longer than 60 seconds or any parameter does not reach baseline.

---

## 11. CURRENT SYSTEM STATE ASSESSMENT

As of the effective date of this specification, the NZT-48 system is in the following state:

| Dimension | Current State | MRM Implication |
|-----------|--------------|-----------------|
| Trades executed | 0 | No learning data exists. All models at NASCENT sample state. |
| Learning engine | IDLE | Correct behaviour. No data to learn from. |
| Meta-learner | Active in code, no outputs | M4 MUST remain at RESEARCH stage until 100 trades resolve. |
| Edge ledger | Active in code, empty | M3 statistics are undefined. No influence on any decision. |
| Drift detection | Active in code, no baselines | Baselines cannot be established until 50 trades resolve. Drift detection runs but all results are PRELIMINARY. |
| Guardrails | Active | `risk_rules_touchable: false`, `allowed_auto_changes: []`, `min_sample_for_auto_change: None` -- all correctly configured for zero-trade state. |
| Readiness gate | GATE 0 (OBSERVING) | Correct. No advancement possible until 100+ outcomes, 90+ days, 5+ ACTIONABLE buckets. |

**Assessment:** The system is correctly configured for its current zero-trade state. The primary risk is not current behaviour but future behaviour: as outcomes accumulate, the learning engine could begin influencing decisions prematurely if the governance gates in this specification and NZT48-ANNEX-LLP-001 are not enforced. This specification provides the framework to prevent that.

---

## 12. APPENDIX A: GLOSSARY

| Term | Definition |
|------|-----------|
| **Bounded knob** | A parameter that the learning engine may adjust within a defined range (Section 4) |
| **Locked parameter** | A parameter that no automated process may modify (Section 5) |
| **Baseline** | The default value of a bounded knob as defined in `settings.yaml` or source code |
| **Drift** | The percentage change of a bounded knob from its baseline over a rolling window |
| **Defensive mode (MRM)** | System state where all learning adjustments are reverted and the learning engine is frozen pending manual IC review |
| **Promotion** | Advancement of a model from one pipeline stage to the next (Section 6) |
| **Demotion** | Regression of a model to a lower pipeline stage (Section 6.3) |
| **IC** | Investment Committee -- the governance body responsible for model approval decisions |
| **Gate** | A set of quantitative criteria that must ALL be met for a promotion or activation decision |
| **Sample clock** | The count of resolved trade outcomes for a given model, bucket, or parameter |
| **Wilson CI** | Wilson score confidence interval for binomial proportion; used by edge ledger for win rate bounds |
| **Edge decay** | Statistically significant reduction in a strategy or indicator's predictive power over time |

---

## 13. APPENDIX B: DOCUMENT HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | System Architect | Initial specification |

---

## 14. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Architect | | | |
| Risk Officer | | | |
| IC Chair | | | |

**This specification is BINDING. No self-learning, adaptive, or parameter-optimization component may operate outside the governance framework defined herein. The learning engine MUST NOT advance beyond GATE 0 (OBSERVING) until all acceptance tests (Section 10) have passed and this document has been signed off by all required parties.**
