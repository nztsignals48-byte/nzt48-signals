# OPERATIONAL GOVERNANCE PLAN

**Document ID:** NZT48-ANNEX-OGV-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** DRAFT — Requires sign-off before paper-to-live migration begins
**Scope:** Reproducibility, change control, run manifests, incident response, and paper-to-live migration gates for the NZT-48 trading system

---

## 1. OBJECTIVE

Establish institutional-grade operational governance for the NZT-48 trading system such that:
1. Every trading session is fully reproducible from its run manifest.
2. Every change to configuration, strategy, or core module follows a documented approval process.
3. The transition from paper trading to limited live trading is gated by quantitative criteria that cannot be bypassed.
4. Incidents are handled with a defined response protocol that protects capital and produces actionable post-mortems.

---

## 2. RUN MANIFESTS

### 2.1 Purpose

A run manifest is the system's "flight recorder." It captures everything needed to understand what happened during a trading session, reproduce it, and diagnose failures.

### 2.2 Manifest Structure

Every trading session (defined as one continuous engine run from startup to shutdown or midnight UTC boundary) MUST produce a run manifest file.

**Location:** `data/manifests/manifest_YYYYMMDD_HHMMSS.json`

```json
{
  "manifest_version": "1.0",
  "session_id": "uuid-v4",
  "environment": {
    "mode": "PAPER | LIVE | BACKTEST",
    "system_version": "10.0",
    "config_hash": "sha256 of settings.yaml",
    "git_commit": "abc1234",
    "git_branch": "main",
    "git_dirty": false,
    "universe_hash": "sha256 of active ticker list",
    "python_version": "3.11.x",
    "platform": "linux-x86_64",
    "docker_image": "nzt48:latest",
    "docker_sha": "sha256:..."
  },
  "timing": {
    "start_utc": "2026-02-27T07:00:00Z",
    "end_utc": "2026-02-27T22:30:00Z",
    "duration_hours": 15.5,
    "market_sessions_covered": ["LSE_PRE", "LSE_MAIN", "NYSE_PRE", "NYSE_MAIN", "NYSE_POST"]
  },
  "data_providers": {
    "primary_us": "polygon",
    "primary_lse": "ibkr",
    "fallback_events": [
      {
        "ticker": "QQQ3.L",
        "from_provider": "ibkr",
        "to_provider": "yfinance",
        "start_utc": "2026-02-27T08:15:00Z",
        "end_utc": "2026-02-27T08:22:00Z",
        "reason": "IBKR_TIMEOUT"
      }
    ],
    "data_quality_score": 0.97
  },
  "signals": {
    "total_generated": 12,
    "total_sent": 8,
    "total_filtered": 4,
    "filter_reasons": {
      "LOW_CONFIDENCE": 2,
      "INSUFFICIENT_LIQUIDITY": 1,
      "DEFENSIVE_MODE": 1
    },
    "by_strategy": {
      "S15": {"generated": 8, "sent": 6},
      "S3": {"generated": 0, "sent": 0, "note": "DORMANT"}
    }
  },
  "positions": {
    "opened": 3,
    "closed": 2,
    "still_open_at_end": 1,
    "by_ticker": {
      "QQQ3.L": {"opened": 1, "closed": 1, "pnl_pct": 1.85},
      "NVD3.L": {"opened": 1, "closed": 1, "pnl_pct": -0.72},
      "3LUS.L": {"opened": 1, "closed": 0, "unrealized_pnl_pct": 0.45}
    }
  },
  "pnl": {
    "realized_pnl_gbp": 112.50,
    "unrealized_pnl_gbp": 45.00,
    "total_pnl_gbp": 157.50,
    "equity_start_gbp": 10000.00,
    "equity_end_gbp": 10157.50,
    "daily_return_pct": 1.575,
    "max_drawdown_intraday_pct": 0.82
  },
  "learning": {
    "gate": "GATE_0_OBSERVING",
    "defensive_mode": false,
    "outcomes_resolved_this_session": 2,
    "total_outcomes_lifetime": 47,
    "edge_ledger_last_rebuild": "2026-02-26T22:30:00Z"
  },
  "errors": [
    {
      "timestamp": "2026-02-27T14:32:11Z",
      "severity": "WARNING",
      "module": "data.feed_router",
      "message": "IBKR timeout for QQQ3.L, falling back to yfinance",
      "resolved": true
    }
  ],
  "health_checks": {
    "all_gates_passing": true,
    "data_quality_above_80": true,
    "no_impossible_signals": true,
    "outcome_sla_met": true,
    "kill_switch_triggered": false
  }
}
```

### 2.3 Manifest Generation Rules

1. The manifest is initialized at engine startup with environment data.
2. Throughout the session, counters and arrays are updated in-memory.
3. At engine shutdown (or midnight UTC boundary), the manifest is finalized and written to disk.
4. If the engine crashes, the incomplete manifest is written with `"status": "CRASHED"` and `"crash_error": "..."`.
5. Manifests MUST NOT be edited after creation. They are append-only artifacts.

### 2.4 Manifest Retention

- **Hot storage:** Last 90 days of manifests in `data/manifests/`.
- **Cold storage:** All manifests older than 90 days compressed to `data/manifests/archive/manifests_YYYY_MM.tar.gz`.
- **Retention period:** Indefinite. Manifests are never deleted.

---

## 3. CHANGE CONTROL

### 3.1 Scope

Change control applies to ANY modification of the following critical files and directories:

| Category | Files/Directories |
|----------|-------------------|
| **Configuration** | `config/settings.yaml` |
| **Strategies** | `strategies/*.py` |
| **Signal Engine** | `signal_engine/*.py` |
| **Learning Engine** | `learning/*.py` |
| **Core Modules** | `main.py`, `data/*.py`, `uk_isa/*.py` |
| **Deployment** | `docker-compose.yml`, `Dockerfile`, `.env` |

### 3.2 Change Control Process

Every change to a controlled file MUST follow this process:

```
1. DESCRIBE  ──→  2. BACKTEST  ──→  3. PAPER TEST  ──→  4. APPROVE  ──→  5. DEPLOY
```

#### Step 1: DESCRIBE

Create a change description document:

```markdown
# CHANGE REQUEST: {CR-YYYY-NNN}

**Date:** YYYY-MM-DD
**Author:** {name}
**Files Modified:** {list of files}

## Description
{What is being changed and why}

## Risk Assessment
- Impact on signal generation: HIGH | MEDIUM | LOW | NONE
- Impact on position sizing: HIGH | MEDIUM | LOW | NONE
- Impact on risk controls: HIGH | MEDIUM | LOW | NONE
- Reversibility: EASY | MODERATE | DIFFICULT | IRREVERSIBLE

## Rollback Plan
{How to revert if the change causes problems}
```

**Location:** `data/change_requests/CR-YYYY-NNN.md`

#### Step 2: BACKTEST

- Run the modified code against at least 30 calendar days of historical data from `research.db`.
- Record backtest results:
  - Total signals generated (before vs after change)
  - Win rate (before vs after)
  - Sharpe ratio (before vs after)
  - Maximum drawdown (before vs after)
- **Gate:** If any of these metrics degrades by more than 10% relative, the change MUST be justified in writing or rejected.

**Location:** Backtest results appended to the change request document.

#### Step 3: PAPER TEST

- Deploy the change to the paper trading environment.
- Run for **5 complete trading sessions** (5 market days).
- Monitor for:
  - Any impossible signals (price outside range, infinite confidence, etc.)
  - Any errors in logs
  - Any data quality degradation
  - Telegram alerts functioning correctly
- **Gate:** Zero impossible signals. Zero unhandled errors.

#### Step 4: APPROVE

- Operator reviews the change request, backtest results, and paper test results.
- Signs off with: `APPROVED: {date} {name}` at the bottom of the CR document.
- If rejected: `REJECTED: {date} {name} — Reason: {reason}`

#### Step 5: DEPLOY

- Commit to git with message referencing the CR number: `[CR-YYYY-NNN] {description}`
- Deploy via `docker-compose build && docker-compose up -d`
- Verify deployment via health check endpoint
- Monitor first trading session closely

### 3.3 Emergency Changes

For urgent fixes (system crash, data corruption, critical bug):

1. Fix may be deployed immediately to paper trading WITHOUT backtest.
2. The change request document MUST still be created within 24 hours (retroactive).
3. The backtest MUST be run within 72 hours (retroactive).
4. If the retroactive backtest shows degradation > 10%, the change must be revised or reverted.

### 3.4 Exempt Changes

The following do NOT require change control:

- Documentation-only changes (`.md` files, comments)
- Dashboard cosmetic changes (CSS, labels, non-functional UI)
- Test files (`tests/*.py`)
- Adding tickers to the universe (governed by UNIVERSE_GOVERNANCE_PLAN)

---

## 4. PAPER-TO-LIVE MIGRATION GATES

### 4.1 Gate Progression

```
PAPER STABLE (30 sessions)
        │
        ▼
PAPER READY (60 sessions)
        │
        ▼
LIMITED LIVE (ongoing)
        │
        ▼
FULL LIVE (future)
```

### 4.2 GATE 1: PAPER STABLE

**Duration:** 30 complete trading sessions (30 market days).

**Quantitative Criteria (ALL must be met):**

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| System uptime | >= 95% of market hours | `sum(session_duration) / sum(expected_market_hours)` |
| Crash-free sessions | >= 28 of 30 (93%) | Count of manifests with `status != "CRASHED"` |
| Impossible signals | 0 | Count of signals failing sanity checks |
| All gates passing | 100% of sessions | `health_checks.all_gates_passing == true` in all manifests |
| Data quality | >= 80% in every session | `data_providers.data_quality_score >= 0.80` |
| Outcome SLA compliance | >= 90% | Outcomes resolved within 24h / total outcomes |
| Kill switch activations | 0 | Must not trigger during paper stable period |

**Qualitative Criteria:**
- All workstream remediation items from the latest audit are addressed.
- Run manifests are being generated correctly for every session.
- Telegram alerts are functional (tested with at least 2 intentional test signals).

**Sign-off:** `data/migration_gate_signoffs/GATE1_PAPER_STABLE.md`

### 4.3 GATE 2: PAPER READY

**Duration:** 60 complete trading sessions (cumulative; includes the 30 from PAPER STABLE).

**Quantitative Criteria (ALL must be met):**

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| Win rate | >= 40% | Resolved outcomes: wins / (wins + losses) |
| Sharpe ratio | >= 0.5 | Annualized from daily returns |
| Maximum drawdown | <= 10% | Peak-to-trough equity drawdown |
| Average daily PnL | > 0 | Mean daily PnL across all 60 sessions |
| Profit factor | >= 1.2 | Gross profit / gross loss |
| Signal quality | >= 70% actionable | Signals sent / signals generated |
| Data quality | >= 85% average | Mean data quality score across sessions |
| Learning loop | GATE 1 (CONTRIBUTING) reached | Per LEARNING_LOOP_PLAN |
| Change control compliance | 100% | All changes during period followed CR process |

**Qualitative Criteria:**
- Operator has reviewed at least 20 individual trade outcomes and confirmed the system's reasoning is sound.
- Edge ledger shows positive expectancy in at least 2 strategy buckets.
- No unresolved audit findings from the most recent system review.

**Sign-off:** `data/migration_gate_signoffs/GATE2_PAPER_READY.md`

### 4.4 GATE 3: LIMITED LIVE

**Entry Requirements:**
- GATE 1 and GATE 2 signed off.
- IBKR brokerage account funded with real capital.
- ISA wrapper confirmed active.
- Tax reporting setup confirmed.
- Emergency contacts and kill switch procedures documented and tested.

**Operating Rules:**

| Parameter | Limited Live Rule |
|-----------|-------------------|
| **Capital deployed** | 10% of target capital (e.g., 1,000 of 10,000) |
| **Risk rules** | Identical to paper (same stops, same position sizing, same drawdown limits) |
| **Max positions** | 2 concurrent (vs paper unlimited) |
| **Max daily loss** | 1% of deployed capital (10) |
| **Review cadence** | Every 5 trading sessions: full performance review |
| **Escalation to full live** | After 60 limited live sessions with positive cumulative PnL |

**5-Session Review Template:**

```markdown
# LIMITED LIVE REVIEW: Sessions {N} to {N+4}

**Period:** YYYY-MM-DD to YYYY-MM-DD
**Capital Deployed:** GBP X,XXX

## Performance
- Trades: X
- Win rate: XX%
- Net PnL: GBP +/-XX.XX
- Max drawdown: X.XX%
- Sharpe (annualized): X.XX

## Issues
- [List any errors, unexpected behaviors, or concerns]

## Decision
- [ ] CONTINUE: Performance acceptable, continue limited live.
- [ ] PAUSE: Concerns identified, return to paper for investigation.
- [ ] ESCALATE: Outstanding performance, request approval to increase capital.
- [ ] HALT: Unacceptable performance or risk, return to PAPER STABLE gate.

**Reviewed by:** {name}
**Date:** YYYY-MM-DD
```

**Location:** `data/limited_live_reviews/REVIEW_{date}.md`

### 4.5 GATE 4: FULL LIVE (Future)

Not yet defined. Requires:
- 60+ limited live sessions with positive cumulative PnL.
- Max drawdown never exceeding 8% during limited live.
- Learning loop at GATE 2 (AUTHORITATIVE).
- Separate full governance document for full live operations.

---

## 5. REPRODUCIBILITY

### 5.1 Deterministic Replay Requirement

Given a run manifest and the corresponding git commit, an operator MUST be able to replay that session and produce **identical signals** (not identical PnL, since market data may differ in replay).

### 5.2 Reproducibility Implementation

1. **Deterministic mode:** A `DETERMINISTIC=true` environment variable forces:
   - All random number generators seeded with the session_id hash.
   - Timestamp-dependent logic uses the manifest's `start_utc` as the base.
   - Data fetched from `research.db` (historical) instead of live providers.

2. **Config snapshot:** The exact `settings.yaml` used in the session is embedded (hash) in the manifest. The full file is stored alongside the manifest as `manifest_YYYYMMDD_HHMMSS_config.yaml`.

3. **Code snapshot:** The git commit hash ensures the exact code version is recoverable.

4. **Data snapshot:** For full reproducibility, the live data received during the session is logged to `data/session_data/{session_id}/` as CSV files. This is optional and can be disabled to save storage (estimated 10MB per session).

### 5.3 Replay Process

```bash
# 1. Checkout exact code version
git checkout {manifest.environment.git_commit}

# 2. Use exact config
cp data/manifests/manifest_YYYYMMDD_HHMMSS_config.yaml config/settings.yaml

# 3. Run in deterministic mode with historical data
DETERMINISTIC=true SESSION_REPLAY={session_id} python main.py

# 4. Compare output signals with manifest
python scripts/compare_replay.py \
  --manifest data/manifests/manifest_YYYYMMDD_HHMMSS.json \
  --replay_output data/replays/{session_id}/
```

### 5.4 Replay Acceptance Criteria

- Signal count must match exactly (generated and filtered).
- Signal tickers must match exactly.
- Signal directions must match exactly.
- Confidence scores must match within +/- 1 point (floating point tolerance).
- If any signal diverges, the replay is FAILED and the reproducibility gap must be investigated.

---

## 6. INCIDENT RESPONSE

### 6.1 Incident Classification

| Severity | Definition | Response Time |
|----------|-----------|--------------|
| **SEV-1 (Critical)** | System producing impossible outputs, live capital at risk, kill switch triggered, data corruption | Immediate (within 15 minutes) |
| **SEV-2 (High)** | System degraded but operational, fallback data in use, drift detected, missed signals | Within 2 hours |
| **SEV-3 (Medium)** | Non-critical error, cosmetic PDF issue, slow performance, minor data gap | Within 24 hours |
| **SEV-4 (Low)** | Documentation error, enhancement request, non-urgent improvement | Within 1 week |

### 6.2 SEV-1 Response Protocol

```
1. DETECT    ──→  Kill switch activates (auto or manual)
2. CONTAIN   ──→  All trading halted. No new positions. Existing positions frozen.
3. DIAGNOSE  ──→  Check last manifest. Check error logs. Identify root cause.
4. FIX       ──→  Apply emergency fix (exempt from full change control; retroactive CR required).
5. VERIFY    ──→  Run in DETERMINISTIC mode to verify fix produces correct output.
6. RESTORE   ──→  Delete kill switch file. Resume paper trading.
7. DOCUMENT  ──→  Post-mortem within 24 hours.
```

### 6.3 Post-Mortem Template

```markdown
# INCIDENT POST-MORTEM: INC-YYYY-NNN

**Date of Incident:** YYYY-MM-DD HH:MM UTC
**Severity:** SEV-X
**Duration:** X hours Y minutes
**Impact:** {What was affected — signals, positions, data, capital}

## Timeline
- HH:MM: {First anomaly detected}
- HH:MM: {Kill switch activated / containment action}
- HH:MM: {Root cause identified}
- HH:MM: {Fix applied}
- HH:MM: {System restored}

## Root Cause
{Detailed technical explanation}

## Contributing Factors
- {Factor 1}
- {Factor 2}

## Impact Assessment
- Signals affected: X
- Positions affected: X
- Capital impact: GBP +/- X.XX
- Data quality impact: X%

## Corrective Actions
| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| {Fix 1} | | | |
| {Fix 2} | | | |

## Preventive Actions
| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| {Prevent 1} | | | |
| {Prevent 2} | | | |

## Lessons Learned
{What did we learn? What would we do differently?}
```

**Location:** `data/incidents/INC-YYYY-NNN.md`

### 6.4 Impossible Output Definition

An output is classified as "impossible" if ANY of the following are true:

| Condition | Example |
|-----------|---------|
| Confidence > 100 or < 0 | confidence = 150 |
| Entry price <= 0 | entry = -5.20 |
| Stop loss on wrong side of entry | LONG with stop above entry |
| Target below entry for LONG | LONG entry=50, T1=45 |
| Position size > 100% of equity | size = 15000 on 10000 account |
| Ticker not in active universe | Signal for "AAPL" when not in universe |
| Signal during market closure | Signal at 03:00 UTC on a weekday for NYSE |
| NaN or Infinity in any numeric field | confidence = NaN |
| Duplicate signal ID | Two signals with same UUID |

Any impossible output triggers immediate SEV-1 response.

---

## 7. FAILURE MODES

| # | Failure Mode | Impact | Mitigation |
|---|-------------|--------|------------|
| FM-1 | Manifest not generated (engine crash at startup) | No audit trail for session | Write partial manifest on crash; health check verifies manifest exists |
| FM-2 | Config hash mismatch (settings.yaml edited during session) | Reproducibility lost | Lock settings.yaml during session; hash checked at startup and hourly |
| FM-3 | Git dirty state in production | Cannot reproduce exact code | Refuse to start if `git_dirty: true` in LIVE mode (warning only in PAPER) |
| FM-4 | Change deployed without backtest | Unknown impact on performance | Change control audit; manifests track config_hash to detect unauthorized changes |
| FM-5 | Paper-to-live gate bypassed | Live trading without sufficient validation | Gate sign-offs required; system refuses LIVE mode without GATE2_PAPER_READY.md existing |
| FM-6 | Kill switch file deleted prematurely | Trading resumes before investigation complete | Kill switch deletion requires post-mortem document to exist |
| FM-7 | Incident not documented | Repeated failures, no organizational learning | Telegram bot prompts for post-mortem if kill switch was active |
| FM-8 | Manifest storage fills disk | New manifests cannot be written | Archive old manifests monthly; monitor disk space; alert at 80% |
| FM-9 | Deterministic replay produces different results | Reproducibility claim is false | Investigate non-deterministic code paths (timestamps, random, external API calls) |
| FM-10 | Emergency change introduces regression | Quick fix makes things worse | Retroactive backtest within 72h; revert if degradation > 10% |

---

## 8. ACCEPTANCE TESTS

### AT-1: Run Manifests

- [ ] Start engine, run for 1 hour, stop engine. Verify manifest file is created at `data/manifests/`.
- [ ] Verify manifest contains all required fields (environment, timing, signals, positions, pnl, errors, health_checks).
- [ ] Verify `config_hash` matches the SHA-256 of `settings.yaml`.
- [ ] Verify `git_commit` matches the current HEAD.
- [ ] Simulate engine crash (kill -9). Verify partial manifest is written with `status: "CRASHED"`.
- [ ] After 90 days, verify old manifests are archived to compressed files.

### AT-2: Change Control

- [ ] Create a change request for a test modification. Verify CR document is created.
- [ ] Run backtest for the change. Verify results are appended to the CR.
- [ ] Deploy to paper. Run 5 sessions. Verify no impossible signals.
- [ ] Approve the change. Verify approval signature in CR document.
- [ ] Attempt to deploy a change without a CR. Verify the system logs a WARNING (enforcement is process-based, not code-enforced, but logging aids audit).

### AT-3: Migration Gates

- [ ] In PAPER mode after 25 sessions, attempt to switch to LIVE. Verify system refuses (need 30).
- [ ] After 30 qualifying sessions, verify GATE 1 criteria can be evaluated from manifests.
- [ ] Create `GATE1_PAPER_STABLE.md` sign-off. Verify system acknowledges gate passage.
- [ ] After 60 sessions meeting GATE 2 criteria, create `GATE2_PAPER_READY.md`. Verify acknowledged.
- [ ] Attempt to set `mode: "LIVE"` without GATE2 sign-off. Verify system refuses to start.

### AT-4: Reproducibility

- [ ] Complete one paper trading session. Record the manifest.
- [ ] Run a deterministic replay using the manifest. Verify signals match within tolerance.
- [ ] Introduce a deliberate code change. Replay again. Verify signals DIVERGE (proving the replay detects changes).

### AT-5: Incident Response

- [ ] Inject an impossible signal (confidence = 200). Verify kill switch activates automatically.
- [ ] Verify Telegram alert is sent within 60 seconds.
- [ ] Attempt to resume trading before creating post-mortem. Verify system logs WARNING.
- [ ] Create post-mortem document. Delete kill switch. Verify system resumes.

---

## 9. PROOF ARTIFACTS

| # | Artifact | Location | Purpose |
|---|----------|----------|---------|
| PA-1 | Run manifest generator | `ops/manifest_writer.py` (new) | Automated manifest creation |
| PA-2 | Manifest archive script | `ops/archive_manifests.sh` (new) | Monthly compression |
| PA-3 | Change request template | `data/change_requests/TEMPLATE.md` | Standardized CR format |
| PA-4 | Change request archive | `data/change_requests/CR-YYYY-NNN.md` | All change records |
| PA-5 | Migration gate sign-offs | `data/migration_gate_signoffs/` | Gate passage proof |
| PA-6 | Limited live review reports | `data/limited_live_reviews/` | 5-session reviews |
| PA-7 | Incident post-mortems | `data/incidents/INC-YYYY-NNN.md` | Failure documentation |
| PA-8 | Deterministic replay script | `scripts/compare_replay.py` (new) | Reproducibility verification |
| PA-9 | Config snapshot per session | `data/manifests/*_config.yaml` | Exact config used |
| PA-10 | Acceptance test suite | `tests/test_ops_governance.py` | Automated governance checks |
| PA-11 | Gate enforcement module | `ops/gate_enforcer.py` (new) | Prevents LIVE mode without gate sign-offs |

---

## 10. OPERATIONAL CALENDAR

| Frequency | Activity | Owner |
|-----------|----------|-------|
| **Every session** | Run manifest generated automatically | System |
| **Every 5 sessions** | Limited live review (when in LIMITED LIVE) | Operator |
| **Weekly** | Review open change requests and incident backlog | Operator |
| **Monthly** | Universe audit (per UNIVERSE_GOVERNANCE_PLAN) | Operator |
| **Monthly** | Archive old manifests | Automated script |
| **Quarterly** | Meta learner review (per LEARNING_LOOP_PLAN) | Operator |
| **Quarterly** | Full system review (config, strategies, risk parameters) | Operator |
| **Annually** | ISA eligibility re-verification for all tickers | Operator |

---

## 11. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Operator | | | |
| Ops Governance Reviewer | | | |

**This plan must be signed off before any paper-to-live migration is attempted. The system MUST NOT run in LIVE mode without GATE 1 and GATE 2 sign-offs on file.**

---

## ADDENDUM: WIRING DRIFT CHANGE CONTROL (W13)

**Added by**: `docs/ADDENDUM_ALWAYS_WIRED_110.md` v1.0

### 12. Wiring Drift Prevention

Any code or configuration change that could break a wiring path (see `annexes/WIRING_TEST_MATRIX.md`) requires additional governance:

#### 12.1 Wiring-Impacting Changes

The following changes are classified as "wiring-impacting" and require the full change control process PLUS a wiring test run:

| Change Type | Example | Required Verification |
|------------|---------|----------------------|
| Artifact schema change | Add/remove field in plays.json | All consumers (Telegram, PDF, War Room) tested against new schema |
| New output channel | Add email delivery | Integration contract updated; wiring path added to matrix |
| Provider swap | yfinance → Polygon | Provenance chain verified; single-source policy confirmed |
| Scheduler job change | Change scan interval | All downstream timing assumptions verified |
| API endpoint change | Modify /api/system_state response | Dashboard consumers tested; schema validation updated |
| Docker/deploy change | New Dockerfile, new bind mount | Container parity check; startup gate verified |

#### 12.2 Wiring Test Gate

Before any wiring-impacting change is deployed:

1. **Pre-deploy**: Run wiring test matrix against proposed change (via Change Impact Simulator if available)
2. **Post-deploy**: Run full wiring test matrix within 15 minutes of deployment
3. **Verification**: All 10 wiring paths must PASS; any FAIL → immediate rollback
4. **Evidence**: wiring_test_results.json archived with deployment manifest

#### 12.3 Continuous Integrity Monitor as Change Guard

The Continuous Integrity Monitor (see `annexes/CONTINUOUS_INTEGRITY_MONITOR_SPEC.md`) serves as the runtime counterpart to the wiring test gate:
- If a change breaks wiring at runtime, the monitor detects it within 5 minutes
- System enters DEGRADED mode automatically
- Operator receives alert with specific drift signature
- Rollback can be initiated via feature flag (< 1 minute)

#### 12.4 Updated Operational Calendar

| Frequency | Activity | Owner |
|-----------|----------|-------|
| **Every deploy** | Run wiring test matrix post-deploy | Operator |
| **Every 5 min** | Continuous integrity monitor (automated) | System |
| **Every session boot** | Startup readiness gate (automated) | System |
| **Weekly** | Review integrity alert log and incident library | Operator |
| **Monthly** | Review and update wiring test matrix for new paths | Operator |
