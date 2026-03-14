# NZT-48 Paper-to-Limited-Live Gates

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-PTLG-001           |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- IC/PM Gate Control |
| Supersedes      | OPS_GOVERNANCE_PLAN.md Section 8 (informal gate criteria) |

---

## 1. PURPOSE

Define the explicit, measurable gate criteria for transitioning the NZT-48 system from paper trading to limited live trading, and ultimately to full live trading. Each gate has quantitative thresholds that cannot be bypassed. Progression requires formal IC/PM sign-off with evidence.

**Principle:** No gate may be skipped. No threshold may be waived. Regression to a previous gate is mandatory if conditions are violated.

---

## 2. GATE OVERVIEW

```
GATE 0: PIPELINE OPERATIONAL
    "The system works end-to-end"
         |
         | (no minimum session count)
         v
GATE 1: PAPER STABLE (minimum 30 trading sessions)
    "The system is reliable"
         |
         | (minimum 60 additional sessions)
         v
GATE 2: PAPER READY (minimum 60 trading sessions from Gate 1)
    "The system is profitable"
         |
         | (IC sign-off required)
         v
GATE 3: LIMITED LIVE (10% capital = 1,000)
    "The system trades real money under tight constraints"
         |
         | (minimum 90 sessions at LIMITED LIVE)
         v
GATE 4: FULL LIVE (requirements TBD after Gate 3 data)
    "The system trades at full capacity"
```

---

## 3. GATE 0: PIPELINE OPERATIONAL

### 3.1 Objective

Verify that the complete system pipeline works end-to-end: data in, signals generated, virtual trades executed, outputs delivered, monitoring operational.

### 3.2 Criteria

| # | Criterion | Threshold | Measurement |
|---|-----------|-----------|-------------|
| G0-1 | All 13 workstreams (W0-W12) code deployed to EC2 | 100% deployed | Parity check PASS for all workstream files |
| G0-2 | Signal pipeline produces at least 1 signal per session | >= 1 signal | `system_state.json` -> `signals_emitted_today >= 1` |
| G0-3 | Virtual trader opens and closes positions | >= 1 round-trip trade | `virtual_trades` table shows OPEN -> CLOSED transition |
| G0-4 | All 7 PDF types generate and pass QA | 7/7 pass | `pdf_qa_log.jsonl` shows QA PASS for each type |
| G0-5 | Telegram delivery success rate > 90% | > 90% | `telegram_debug.jsonl` sent / (sent + failed) |
| G0-6 | War Room dashboard fully wired (37 panels) | 37/37 render | Playwright wiring proof pack PASS (see WAR_ROOM_REQUIREMENTS_SPEC.md Section F) |
| G0-7 | Go-Live Gate page shows all 8 checks | 8/8 registered | `GET /api/go-live-gate` returns 8 checks |

### 3.3 Sign-Off

| Role | Authority |
|------|----------|
| Operator | Self-certification with evidence pack |

### 3.4 Evidence Required

- Parity check output showing all PASS
- Screenshot of War Room with data flowing
- Sample signal in Telegram
- Sample PDF (clean, no DRAFT watermark)
- Virtual trade record showing open + close
- Go-Live Gate screenshot showing 8 checks registered

### 3.5 Rollback Criteria

Gate 0 has no rollback (it is the starting state).

---

## 4. GATE 1: PAPER STABLE

### 4.1 Objective

Demonstrate that the system operates reliably over a sustained period. Focus on uptime, crash-free operation, and absence of impossible outputs.

### 4.2 Minimum Measurement Period

**30 trading sessions** (approximately 6 weeks, counting only days when LSE is open and the system was running during market hours).

### 4.3 Criteria

| # | Criterion | Threshold | Measurement | Evidence |
|---|-----------|-----------|-------------|---------|
| G1-1 | System uptime during LSE hours (08:00-16:30 UK) | >= 95% | `(total_minutes_running / total_lse_minutes) * 100` | Uptime log per session |
| G1-2 | Crash-free rate | >= 93% | `(sessions_without_crash / total_sessions) * 100` | Engine restart log |
| G1-3 | Zero impossible signals (>+-30% session return) reaching output | 0 events | Scan `telegram_debug.jsonl` and `output_policy.log` for SANITY_FAIL events that were NOT caught | Output gate audit |
| G1-4 | Zero regime contradictions reaching output | 0 events | Scan `output_policy.log` for REGIME_MISMATCH events that were NOT caught | Consistency audit |
| G1-5 | All 8 Go-Live Gate checks PASS simultaneously | 8/8 PASS at least once per session | `go_live_gate_log.jsonl` | Gate evaluation log |
| G1-6 | Data coverage >= 80% consistently | >= 80% in 90%+ of sessions | `system_state.json` -> `data_reliability` field | Per-session coverage log |
| G1-7 | Feature flags operational | All 13 flags toggle correctly | Weekly flag toggle drill results | Drill log |

### 4.4 Sign-Off

| Role | Authority |
|------|----------|
| Portfolio Manager | Review and approve evidence pack |

### 4.5 Evidence Required

- 30-session uptime report with per-session breakdown
- Crash log (or confirmation of zero crashes)
- Output gate audit showing zero undetected impossible signals
- Consistency audit showing zero undetected regime contradictions
- Go-Live Gate daily evaluation history
- Data coverage trending report
- Weekly drill log for feature flag toggles
- All 38 IC-level acceptance tests PASS (see ACCEPTANCE_TESTS_MASTER.md)

### 4.6 Rollback Criteria (Demotion to Gate 0)

| Condition | Action |
|-----------|--------|
| Uptime drops below 90% for 5 consecutive sessions | Demote to Gate 0. Root cause investigation. |
| Any impossible signal reaches Telegram undetected | Demote to Gate 0. Mandatory SEV-1 postmortem. |
| 3+ engine crashes in a single session | Demote to Gate 0. LKG restore and investigation. |

---

## 5. GATE 2: PAPER READY

### 5.1 Objective

Demonstrate that the system generates profitable trading signals consistently and that the learning engine is stable. This gate proves the system can make money, not just operate reliably.

### 5.2 Minimum Measurement Period

**60 trading sessions** measured from Gate 1 approval date (approximately 12 weeks).

### 5.3 Criteria

| # | Criterion | Threshold | Measurement | Evidence |
|---|-----------|-----------|-------------|---------|
| G2-1 | Win rate | >= 40% | `winning_trades / total_resolved_trades * 100` | Trade outcome log |
| G2-2 | Sharpe ratio (annualised) | >= 0.5 | `(mean_daily_return / std_daily_return) * sqrt(252)` | Performance analytics |
| G2-3 | Maximum drawdown | <= 10% | Peak-to-trough equity decline | Equity curve with drawdown overlay |
| G2-4 | Average R-multiple | >= 0.8 | `mean(trade_pnl / trade_risk)` across all resolved trades | Per-trade R-multiple log |
| G2-5 | Resolved trade count | >= 100 | Total trades with definitive outcome (win or loss, not pending) | Trade outcome log |
| G2-6 | Learning engine stability | No drift alerts in final 20 sessions | Drift detection log shows zero DRIFT_DETECTED events | Learning state audit |
| G2-7 | All acceptance tests passing | 38/38 IC-level tests PASS | ACCEPTANCE_TESTS_MASTER.md verification | Test results with proof artifacts |
| G2-8 | Compliance checklist (Gate 2) complete | All Gate 2 items checked | COMPLIANCE_NOTES.md Section 7 | Signed checklist |

### 5.4 Sign-Off

| Role | Authority |
|------|----------|
| Portfolio Manager | Review performance evidence, approve readiness |
| IC Member | Co-approve with PM |

### 5.5 Evidence Required

- 60-session performance report: daily P&L, cumulative equity, Sharpe ratio, win rate, R-multiples
- Equity curve with drawdown overlay
- Trade-by-trade outcome log (all 100+ resolved trades)
- Learning engine stability report (drift score history)
- Acceptance test results with all 38 proof artifacts
- Compliance checklist (Gate 2 section of COMPLIANCE_NOTES.md)
- Data vendor migration plan status (Polygon.io or IBKR evaluation)

### 5.6 Rollback Criteria (Demotion to Gate 1)

| Condition | Action |
|-----------|--------|
| Win rate drops below 30% over any rolling 20-session window | Demote to Gate 1. Strategy review. |
| Max drawdown exceeds 15% | Demote to Gate 1. Risk parameter review. |
| Learning engine drift alert fires | Demote to Gate 1. Learning loop investigation. Drift must be resolved and 20 clean sessions logged before re-attempting Gate 2. |
| Any Gate 1 criteria violated | Immediate demotion to Gate 1. |

---

## 6. GATE 3: LIMITED LIVE

### 6.1 Objective

Deploy the system with real capital under tight constraints. Validate that paper performance translates to live execution with real market impact, slippage, and broker integration.

### 6.2 Capital Allocation

| Parameter | Value |
|-----------|-------|
| Allocated capital | 1,000 (10% of target 10,000 equity) |
| Maximum concurrent positions | 2 |
| Daily loss limit | 10 (1% of allocated capital) |
| Per-trade risk | 5 maximum (0.5% of allocated capital) |
| Weekly review cycle | Every Friday with PM |

### 6.3 Prerequisites (Before Entry)

| # | Prerequisite | Status |
|---|-------------|--------|
| P3-1 | IC sign-off (PM + IC member both approve) | Required |
| P3-2 | Commercial data provider active (not yfinance) | Required |
| P3-3 | Broker API integration tested in broker's paper mode | Required |
| P3-4 | Kill switch tested and verified on live broker API | Required |
| P3-5 | ISA eligibility of all instruments confirmed with provider | Required |
| P3-6 | Compliance checklist (Gate 3) complete | Required |
| P3-7 | Emergency flatten tested on broker paper mode | Required |
| P3-8 | Legal review of automated ISA trading completed | Required |

### 6.4 Operating Constraints

| Constraint | Value | Enforcement |
|-----------|-------|------------|
| Max position size | 500 (50% of allocated capital) | Hard-coded limit in execution module |
| Max concurrent positions | 2 | Enforced by virtual trader (carried to live) |
| Daily loss limit | 10 | Circuit breaker L1 at 1% of allocated capital |
| Weekly loss limit | 30 | Circuit breaker at 3% of allocated capital per week |
| Minimum confidence for live execution | 70 (raised from 60 in paper) | Gate threshold adjustment |
| Minimum score for live execution | 20 (raised from 10 in paper) | Gate threshold adjustment |

### 6.5 Criteria for Continued Operation

Monitored continuously during the minimum 30-session measurement period:

| # | Criterion | Threshold | Measurement |
|---|-----------|-----------|-------------|
| G3-1 | Minimum sessions at Limited Live | 30 | Session count since Gate 3 entry |
| G3-2 | Broker execution success rate | >= 95% | Successful orders / total orders |
| G3-3 | Slippage within acceptable bounds | Average slippage < 0.3% | `(executed_price - expected_price) / expected_price` |
| G3-4 | Kill switch works on live broker | Tested weekly | Weekly drill log |
| G3-5 | Daily loss limit never breached | 0 breaches | Broker account statement |
| G3-6 | System uptime >= 98% during LSE hours | >= 98% | Uptime log (higher bar than paper) |
| G3-7 | Positive P&L in >= 50% of weeks | >= 50% | Weekly P&L summary |

### 6.6 Sign-Off

| Role | Authority |
|------|----------|
| Portfolio Manager | Weekly review and continuation approval |
| IC Chair | Gate 3 entry approval + monthly review |
| Risk Officer | Gate 3 entry approval + ongoing monitoring |

### 6.7 Evidence Required

- Broker account statements (weekly)
- Execution quality report (slippage, fill rates)
- Kill switch drill results (weekly)
- P&L attribution (per trade, per strategy)
- System uptime report (per session)
- Incident log (all incidents during live trading)

### 6.8 Rollback Criteria (Demotion to Gate 2)

| Condition | Action |
|-----------|--------|
| Daily loss limit breached (10 loss in a single day) | Immediate kill switch. PM review within 24 hours. If systemic: demote to Gate 2. |
| Weekly loss limit breached (30 loss in a single week) | Kill switch. PM + IC review within 48 hours. Demote to Gate 2. |
| Broker execution failure rate > 10% | Suspend live trading. Investigate broker integration. |
| Average slippage > 0.5% | Suspend live trading. Investigate execution quality. |
| Any Gate 1 or Gate 2 criteria violated | Demote to appropriate gate. |
| Kill switch fails to activate during weekly drill | Immediate suspension. Do not resume until fix verified. |

---

## 7. GATE 4: FULL LIVE

### 7.1 Objective

Operate the system at full capital capacity with enhanced monitoring and operational procedures.

### 7.2 Note

Full Gate 4 requirements will be defined after Gate 3 data is available. The criteria below are preliminary and subject to revision based on Gate 3 performance.

### 7.3 Preliminary Criteria

| # | Criterion | Threshold | Notes |
|---|-----------|-----------|-------|
| G4-1 | Minimum sessions at Limited Live | 90 | 3x the Gate 3 minimum |
| G4-2 | Consistent profitability | Positive P&L in 60%+ of weeks during Gate 3 | Based on Gate 3 data |
| G4-3 | Full IC review and approval | PM + IC Chair + Risk Officer | Formal review meeting |
| G4-4 | Enhanced monitoring procedures | 24/7 alerting, oncall rotation (if applicable) | Operational readiness |
| G4-5 | Broker relationship established | Account manager, escalation contacts | Operational |
| G4-6 | Capital allocation reviewed | Full 10,000+ based on Gate 3 performance | PM decision |
| G4-7 | Insurance/risk transfer considered | Evaluate stop-loss insurance, account insurance | PM/IC decision |

### 7.4 Sign-Off

| Role | Authority |
|------|----------|
| Portfolio Manager | Full review and recommendation |
| IC Chair | Final approval authority |
| Risk Officer | Risk assessment and approval |

---

## 8. GATE TRANSITION PROCESS

### 8.1 Requesting Gate Transition

1. Operator assembles the evidence pack for the target gate (per sections above).
2. Operator writes a Gate Transition Memo summarising performance against all criteria.
3. Evidence pack and memo submitted to PM for review.
4. PM reviews within 5 business days.
5. If PM approves, IC member (Gate 2+) reviews within 5 business days.
6. If all approvers sign off, gate transition is recorded in DECISION_REGISTER.md.

### 8.2 Gate Transition Record

Each gate transition is recorded with:

```markdown
## Gate Transition: [Gate N] -> [Gate N+1]

| Field | Value |
|-------|-------|
| Date | YYYY-MM-DD |
| From Gate | N |
| To Gate | N+1 |
| Sessions in Previous Gate | NN |
| Key Metrics | Win rate: X%, Sharpe: Y, Max DD: Z% |
| Evidence Pack Location | artifacts/gate_transitions/gate_N_to_N+1/ |
| PM Approval | Name, Date |
| IC Approval | Name, Date (if applicable) |
| Risk Officer | Name, Date (if applicable) |
| Notes | [Any conditions or concerns] |
```

### 8.3 Demotion Process

1. Demotion condition triggered (per rollback criteria above).
2. Kill switch activated if capital at risk (Gate 3+).
3. Operator documents the demotion event in INCIDENT_LOG.jsonl.
4. PM notified within 4 hours (SEV-1/2) or 24 hours (SEV-3/4).
5. Root cause investigation and postmortem (per POSTMORTEM_LIBRARY_TEMPLATE.md).
6. Demotion recorded in DECISION_REGISTER.md.
7. Re-entry to the demoted-from gate requires a fresh measurement period (no credit for previous sessions).

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial gate specification |

---

*End of Document NZT48-ANNEX-PTLG-001*
