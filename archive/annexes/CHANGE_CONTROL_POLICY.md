# CHANGE CONTROL POLICY

**Document ID:** NZT48-ANNEX-CCP-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** BINDING
**Scope:** All changes to code, configuration, strategy parameters, learning weights, universe composition, infrastructure, and data providers for the NZT-48 leveraged ISA trading system
**Authority:** This document is a binding governance instrument. Non-compliance with any mandatory provision constitutes a policy violation subject to incident review.

---

## TABLE OF CONTENTS

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Change Categories](#2-change-categories)
3. [Change Request Format](#3-change-request-format)
4. [Review and Approval Gates](#4-review-and-approval-gates)
5. [Deployment Pipeline](#5-deployment-pipeline)
6. [Versioning Scheme](#6-versioning-scheme)
7. [Promotion Gates](#7-promotion-gates)
8. [Rollback Discipline](#8-rollback-discipline)
9. [Configuration Change Control](#9-configuration-change-control)
10. [Audit Trail](#10-audit-trail)
11. [Acceptance Tests](#11-acceptance-tests)
12. [Definitions](#12-definitions)
13. [Review Schedule](#13-review-schedule)

---

## 1. PURPOSE AND SCOPE

### 1.1 Purpose

This Change Control Policy establishes the mandatory governance framework for proposing, reviewing, testing, approving, deploying, and rolling back ANY change to the NZT-48 trading system. Its objectives are:

1. **Capital protection.** No change shall be deployed to a live or paper trading environment without a validated rollback path. The system manages leveraged ISA instruments where uncontrolled changes can produce amplified losses.
2. **Reproducibility.** Every deployed state of the system must be traceable to a specific version, configuration hash, and git commit SHA.
3. **Accountability.** Every change must have a named requester, a named approver, and a documented rationale.
4. **Operational continuity.** The deployment pipeline must guarantee that a failed change can be reversed within 5 minutes without data loss.

### 1.2 Scope

This policy governs ALL changes to the following system components:

| Component | Examples | Location |
|---|---|---|
| Application code | Python modules, strategies, delivery logic | `/home/ubuntu/nzt48-signals/*.py`, `strategies/`, `uk_isa/`, `delivery/` |
| Configuration | Strategy parameters, thresholds, schedules | `config/settings.yaml` |
| Universe | Ticker additions, removals, reclassifications | `config/settings.yaml` universe section, UNIVERSE_GOVERNANCE_PLAN |
| Feature flags | Flag toggles (15 flags in `settings.yaml`) | `config/settings.yaml` feature_flags section |
| Strategy parameters | Entry/exit thresholds, position sizing, stop distances | `config/settings.yaml` strategies section |
| Learning weights | Adaptive scoring weights, drift thresholds | Learning loop configuration |
| Infrastructure | Docker configuration, EC2 settings, networking | `docker-compose.yml`, `Dockerfile`, EC2 instance `54.242.32.11` |
| Data providers | Provider configuration, API keys, failover logic | `config/settings.yaml` data_providers section |
| Dashboard | Next.js frontend, API endpoints | `dashboard/` directory |
| Documentation | Plans, specs, annexes | `annexes/`, project root |

### 1.3 Applicability

This policy applies to all personnel who propose, review, approve, or execute changes:

- **Lead Engineer (LE):** Primary technical authority. Proposes and reviews code changes. Approves CAT-1, CAT-2, and CAT-3 changes.
- **Project Manager (PM):** Operational oversight. Co-approves CAT-2 changes. Receives notification on CAT-1 and CAT-3 changes.
- **Investment Committee (IC):** Strategic authority. Signs off on promotion gates and strategy-level changes. Receives notification on CAT-2 changes.
- **Operators:** Personnel who execute deployment steps. Must follow the deployment pipeline exactly as specified.

### 1.4 Precedence

Where this policy conflicts with other annexes, the following precedence applies:

1. This Change Control Policy (CCP)
2. ROLLBACK_PLAN.md (rollback procedures)
3. OPS_GOVERNANCE_PLAN.md (operational governance)
4. UNIVERSE_GOVERNANCE_PLAN.md (universe-specific changes)
5. Individual workstream specifications

---

## 2. CHANGE CATEGORIES

Every change to the system MUST be classified into exactly one of the following four categories. The category determines the required approval chain, review depth, and deployment urgency.

### 2.1 CAT-1: EMERGENCY

**Definition:** A change required to prevent or halt active capital loss, system failure, or data corruption. CAT-1 changes bypass the standard review cycle and are deployed immediately.

**Examples:**
- Kill switch activation (flatten all positions)
- Critical bug fix where the system is generating impossible signals
- Security breach remediation
- Data provider failure requiring immediate failover

**Constraints:**
- Post-hoc review MUST be completed within 24 hours of deployment.
- A post-incident report MUST be filed documenting: root cause, impact assessment, remediation steps, and preventive measures.
- CAT-1 changes that modify strategy logic MUST be followed by a CAT-2 CR within 48 hours to validate the fix under standard review.

### 2.2 CAT-2: STANDARD

**Definition:** A planned change that modifies system behaviour, adds functionality, fixes non-critical bugs, or tunes strategy parameters. CAT-2 changes require the full review cycle.

**Examples:**
- New workstream implementation (W1-W13)
- Strategy logic changes (entry/exit conditions, scoring algorithms)
- New data provider integration
- Bug fixes that do not pose immediate capital risk
- Parameter tuning that alters signal generation behaviour
- Dashboard feature additions

**Constraints:**
- Full review cycle required (see Section 4).
- If the change modifies strategy logic or signal generation, a backtest comparison MUST be provided showing before/after performance metrics.
- Deployment must follow the complete 10-step pipeline (see Section 5).

### 2.3 CAT-3: CONFIGURATION

**Definition:** A change to system configuration that does not alter application logic. CAT-3 changes require lightweight review.

**Examples:**
- Universe changes (adding/removing tickers) subject to UNIVERSE_GOVERNANCE_PLAN
- Threshold adjustments within documented parameter bounds
- Feature flag toggles (e.g., `sanity_gate_v2`, `provenance_tracking`, `regime_unification`)
- Schedule changes (scan intervals, report timing)
- Log level adjustments

**Constraints:**
- Changes MUST remain within documented parameter bounds. Any change that would set a parameter outside its defined range is automatically escalated to CAT-2.
- Feature flag toggles that enable new functionality for the first time require the corresponding workstream's acceptance tests to pass before the flag is set to `true`.
- A backup of the previous `settings.yaml` MUST be taken before any configuration change.

### 2.4 CAT-4: DOCUMENTATION

**Definition:** A change to plans, specifications, reports, or other documentation that does not affect the running system.

**Examples:**
- Annex creation or revision
- Test plan updates
- Architecture documentation
- Meeting notes and decision records

**Constraints:**
- No deployment required.
- Self-approved by the author.
- Must be committed to git with a descriptive commit message.

---

## 3. CHANGE REQUEST FORMAT

Every change of category CAT-1, CAT-2, or CAT-3 MUST be documented as a Change Request (CR) before deployment. CAT-1 changes may be documented post-hoc within 24 hours.

### 3.1 CR Identifier

Format: `CR-YYYYMMDD-NNN`

Where:
- `YYYYMMDD` is the date the CR is raised
- `NNN` is a zero-padded sequential number starting at 001 for each day

Example: `CR-20260227-003` (third CR raised on 27 February 2026)

### 3.2 Required Fields

| Field | Description | Required For |
|---|---|---|
| **CR-ID** | Unique identifier per Section 3.1 | All |
| **Title** | Concise description of the change (max 120 characters) | All |
| **Category** | CAT-1, CAT-2, CAT-3, or CAT-4 | All |
| **Requester** | Name of the person requesting the change | All |
| **Date Raised** | ISO 8601 date (YYYY-MM-DD) | All |
| **Description** | Detailed explanation of what is being changed and why | All |
| **Files Changed** | Exact file paths of all modified files | CAT-1, CAT-2, CAT-3 |
| **Risk Assessment** | Blast radius classification (see Section 3.3) | CAT-1, CAT-2, CAT-3 |
| **Rollback Plan** | Specific rollback mechanism and steps (see Section 8) | CAT-1, CAT-2, CAT-3 |
| **Acceptance Tests** | List of test IDs that validate this change | CAT-2, CAT-3 |
| **Dependencies** | Other CRs or workstreams this change depends on | CAT-2 |
| **Backtest Results** | Before/after performance comparison | CAT-2 (strategy changes only) |

### 3.3 Risk Assessment: Blast Radius

Every CR must classify its blast radius using the following scale:

| Level | Definition | Examples |
|---|---|---|
| **LOW** | Change is isolated to a single module with no downstream effects. Rollback via feature flag. | Log level change, documentation update, single threshold adjustment within bounds |
| **MEDIUM** | Change affects multiple modules or alters output artefacts. Rollback via feature flag or targeted LKG restore. | New feature behind flag, dashboard layout change, universe addition |
| **HIGH** | Change affects signal generation, position sizing, or risk management. Rollback requires LKG restore. | Strategy parameter change, scoring algorithm modification, new data provider |
| **CRITICAL** | Change affects core trading logic, deployment infrastructure, or promotion gate criteria. Rollback requires full LKG restore and manual verification. | Trading mode transition, Docker infrastructure change, kill switch modification |

### 3.4 CR Template

```
CR-ID:          CR-YYYYMMDD-NNN
Title:          [Concise description]
Category:       CAT-[1|2|3|4]
Requester:      [Name]
Date Raised:    YYYY-MM-DD
Status:         DRAFT | REVIEW | APPROVED | DEPLOYED | ROLLED_BACK | REJECTED

--- DESCRIPTION ---
[What is being changed and why]

--- FILES CHANGED ---
- path/to/file1.py
- path/to/file2.yaml

--- RISK ASSESSMENT ---
Blast Radius:   [LOW | MEDIUM | HIGH | CRITICAL]
Justification:  [Why this blast radius was assigned]

--- ROLLBACK PLAN ---
Mechanism:      [Feature flag | LKG restore | Targeted revert]
Steps:
1. [Step 1]
2. [Step 2]
Estimated time: [Minutes]

--- ACCEPTANCE TESTS ---
- [Test ID]: [Description]

--- DEPENDENCIES ---
- [CR-ID or workstream reference]

--- APPROVAL ---
Lead Engineer:  [Name] [Date] [APPROVED|REJECTED]
Project Manager:[Name] [Date] [APPROVED|REJECTED|N/A]
IC:             [Name] [Date] [APPROVED|REJECTED|N/A]
```

---

## 4. REVIEW AND APPROVAL GATES

### 4.1 Approval Matrix

| Category | Lead Engineer | Project Manager | Investment Committee | Backtest Required |
|---|---|---|---|---|
| **CAT-1 EMERGENCY** | APPROVES (immediate) | NOTIFIED within 1 hour | NOTIFIED within 24 hours | No (post-hoc if strategy change) |
| **CAT-2 STANDARD** | APPROVES | APPROVES | INFORMED | Yes, if strategy change |
| **CAT-3 CONFIGURATION** | APPROVES | INFORMED | N/A | No |
| **CAT-4 DOCUMENTATION** | Self-approved by author | N/A | N/A | No |

### 4.2 Review Requirements by Category

**CAT-1 EMERGENCY:**
1. Lead Engineer makes the deploy decision in real time.
2. PM is notified within 1 hour of deployment, including: what was changed, why it was urgent, and current system status.
3. IC is notified within 24 hours with a post-incident summary.
4. A full post-hoc review CR is filed within 24 hours, documenting the emergency change as if it were a standard CR.

**CAT-2 STANDARD:**
1. Requester submits CR with all required fields.
2. Lead Engineer reviews: code quality, test coverage, rollback plan completeness, risk assessment accuracy.
3. PM reviews: operational impact, schedule alignment, resource implications.
4. If the change modifies strategy logic or signal generation:
   - Backtest results MUST be provided comparing at least 60 sessions of before/after performance.
   - Key metrics: win rate, Sharpe ratio, max drawdown, average daily return.
5. IC is informed of the change and its expected impact. IC may escalate to full IC review at their discretion.
6. Both LE and PM must record explicit APPROVED status before deployment proceeds.

**CAT-3 CONFIGURATION:**
1. Requester submits CR with all required fields.
2. Lead Engineer reviews: parameter bounds compliance, feature flag readiness, universe governance compliance.
3. PM is informed of the change.
4. LE records explicit APPROVED status before the configuration change is applied.

**CAT-4 DOCUMENTATION:**
1. Author self-approves.
2. Committed to git with descriptive message.
3. No further review required.

### 4.3 Rejection Protocol

If a CR is rejected at any gate:
1. The rejector MUST provide a written rationale.
2. The CR status is set to REJECTED.
3. The requester may revise and resubmit as a new CR (new CR-ID).
4. Rejected CRs are retained in the audit trail permanently.

---

## 5. DEPLOYMENT PIPELINE

All deployments of CAT-1, CAT-2, and CAT-3 changes MUST follow this 10-step pipeline. No step may be skipped. Each step must complete successfully before proceeding to the next.

### Step 1: Code Change on Development Machine

- All code changes are made on the local development machine (Mac).
- Changes are committed to git with a commit message referencing the CR-ID.
- Commit message format: `[CR-YYYYMMDD-NNN] <descriptive message>`

### Step 2: Local Validation

- Python changes: `python -m py_compile <changed_files>` for syntax validation.
- Configuration changes: YAML syntax validation and parameter bounds check.
- Dashboard changes: `npm run build` in the dashboard directory.
- All pre-existing warnings are acceptable; new warnings introduced by the change are not.

### Step 3: rsync to EC2

Canonical rsync command:

```bash
rsync -avz --delete \
  --exclude='.git' \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='node_modules/' \
  --exclude='.env' \
  --exclude='data/' \
  -e "ssh -i ~/.ssh/nzt48-key.pem" \
  /Users/rr/nzt48-signals/ \
  ubuntu@54.242.32.11:/home/ubuntu/nzt48-signals/
```

- The `--delete` flag ensures the EC2 copy is an exact mirror of the development machine (excluding the listed patterns).
- The `data/` directory is excluded to preserve runtime data, manifests, and logs on the server.

### Step 4: Docker Compose Build

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 \
  "cd /home/ubuntu/nzt48-signals && docker compose build nzt48"
```

- Immutable image tag format: `nzt48:YYYYMMDD-HHMM-<git-short-sha>`
- Example: `nzt48:20260227-1430-a3b7c2d`
- The build must complete without errors. Any build failure halts the pipeline.

### Step 5: Docker Compose Up

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 \
  "cd /home/ubuntu/nzt48-signals && docker compose up -d nzt48"
```

- Container starts in detached mode.
- Wait 10 seconds for initialisation before proceeding to Step 6.

### Step 6: Container Parity Check

Verify that the code inside the running container matches the code on the EC2 host:

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 << 'PARITY'
HOST_MD5=$(find /home/ubuntu/nzt48-signals -name '*.py' -exec md5sum {} + | sort | md5sum)
CONTAINER_MD5=$(docker exec nzt48 find /app -name '*.py' -exec md5sum {} + | sort | md5sum)
if [ "$HOST_MD5" = "$CONTAINER_MD5" ]; then
  echo "PARITY CHECK: PASS"
else
  echo "PARITY CHECK: FAIL"
  exit 1
fi
PARITY
```

- A parity check failure halts the pipeline. The container must be rebuilt (return to Step 4).
- Parity failure is logged as a deployment incident.

### Step 7: Health Check

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 \
  "curl -sf http://localhost:8000/api/health"
```

- The `/api/health` endpoint must return HTTP 200 with a valid JSON response.
- The health response includes: data health badge (GREEN/AMBER/RED), per-ticker status, and failed tickers list.
- A non-200 response or timeout halts the pipeline and triggers the rollback decision matrix (see Section 8).

### Step 8: Smoke Test

- Wait for one complete scan cycle (60 seconds).
- Verify that the scan cycle produces expected artefacts:
  - Log entries indicating successful scan completion.
  - No ERROR-level log entries related to the change.
  - If applicable: signal output, PDF generation, or Telegram delivery as expected.

### Step 9: LKG Tag

If all preceding steps pass:

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 \
  "cd /home/ubuntu/nzt48-signals && git tag lkg-$(date +%Y%m%d-%H%M)-$(git rev-parse --short HEAD)"
```

- The LKG (Last Known Good) tag marks the current state as a known-safe restore point.
- LKG tags are never deleted.
- The LKG tag format is: `lkg-YYYYMMDD-HHMM-<git-short-sha>`

### Step 10: CR Status Update

- Update the CR status to DEPLOYED.
- Record: deployment timestamp, deployer name, LKG tag, Docker image tag.
- Notify relevant stakeholders per the approval matrix (Section 4.1).

---

## 6. VERSIONING SCHEME

### 6.1 Semantic Versioning

The system follows semantic versioning: **MAJOR.MINOR.PATCH**

| Component | Increment When | Examples |
|---|---|---|
| **MAJOR** | Architecture change, new trading mode, ISA-to-LIVE transition, breaking changes to data contracts | 1.0 to 2.0 (V2 momentum upgrade), 2.0 to 3.0 (LIVE transition) |
| **MINOR** | New workstream completion, new strategy added, new data provider integrated, new delivery channel | 2.0 to 2.1 (W1 sanity gate), 2.1 to 2.2 (W2 return math) |
| **PATCH** | Bug fix, parameter tuning, configuration change, documentation update | 2.1.0 to 2.1.1 (threshold adjustment), 2.1.1 to 2.1.2 (bug fix) |

### 6.2 Version Tracking

- All versions are tracked in `CHANGELOG.md` at the project root.
- Each CHANGELOG entry includes: version number, date, CR-ID(s), summary of changes.
- Docker images are tagged with both the version number and git SHA: `nzt48:2.1.3-a3b7c2d`

### 6.3 Version Increment Rules

- Version increments are determined by the highest-impact change in a release.
- Multiple CRs may be bundled into a single version increment if they are deployed together.
- MAJOR version increments require IC sign-off.
- MINOR version increments require PM acknowledgement.
- PATCH version increments require LE approval only.

---

## 7. PROMOTION GATES

The system progresses through four environments. Promotion from one environment to the next requires meeting ALL criteria for that gate. Gates cannot be bypassed.

### 7.1 DEV to PAPER

| Criterion | Threshold | Measured By |
|---|---|---|
| Acceptance tests | All pass for the relevant workstream | Automated test suite |
| Parity check | Clean (host matches container) | Deployment pipeline Step 6 |
| Health check | `/api/health` returns 200, badge GREEN or AMBER | Deployment pipeline Step 7 |
| Smoke test | One scan cycle completes without ERROR | Deployment pipeline Step 8 |

**Approver:** Lead Engineer

### 7.2 PAPER STABLE to PAPER READY

| Criterion | Threshold | Measured By |
|---|---|---|
| Session count | Minimum 30 consecutive trading sessions | Run manifests |
| Uptime | 95% or higher across all sessions | Health check logs |
| Impossible signals | Zero impossible signals generated | Signal validation logs |
| Feature flag stability | No unplanned flag changes during the period | Configuration audit trail |
| Data provider reliability | No unrecoverable data provider failures | Run manifests |

**Approver:** Lead Engineer + PM

### 7.3 PAPER READY to LIMITED LIVE

| Criterion | Threshold | Measured By |
|---|---|---|
| Session count | Minimum 60 consecutive trading sessions in PAPER READY | Run manifests |
| Win rate | >= 40% | Strategy performance report |
| Sharpe ratio | >= 0.5 (annualised) | Strategy performance report |
| Maximum drawdown | <= 10% | Strategy performance report |
| System stability | No CAT-1 incidents in last 30 sessions | Incident log |
| IC sign-off | Explicit written approval | IC meeting minutes |

**Approver:** Lead Engineer + PM + IC (unanimous)

### 7.4 LIMITED LIVE to FULL LIVE

| Criterion | Threshold | Measured By |
|---|---|---|
| Session count | Minimum 90 consecutive trading sessions at LIMITED LIVE | Run manifests |
| Consistent profitability | Positive cumulative P&L over the 90-session window | Trading ledger |
| Win rate | >= 40% sustained | Strategy performance report |
| Sharpe ratio | >= 0.5 sustained | Strategy performance report |
| Maximum drawdown | <= 10% sustained | Strategy performance report |
| Operational stability | No CAT-1 incidents in last 60 sessions | Incident log |
| Full IC review | Comprehensive review of all metrics, risk exposure, and operational readiness | IC review document |

**Approver:** IC (full board review)

### 7.5 Demotion

Promotion gates work in both directions. If a promoted environment fails to maintain its gate criteria:

- **LIMITED LIVE failing gate criteria:** Automatic demotion to PAPER READY. All positions flattened. IC notified within 1 hour.
- **PAPER READY failing gate criteria:** Automatic demotion to PAPER STABLE. Investigation CR raised.
- Demotion does NOT require approval (safety first, review after).

---

## 8. ROLLBACK DISCIPLINE

### 8.1 Fundamental Principle

**Every CR must have a documented rollback path BEFORE deployment.** A CR submitted without a rollback plan MUST be rejected at the review gate (see Acceptance Test CCP-T01).

### 8.2 Rollback Mechanisms (in order of preference)

| Mechanism | Speed | Rebuild Required | When to Use |
|---|---|---|---|
| **Feature flag toggle** | Instant (<60 seconds) | No | Change is behind a feature flag; toggling the flag to `false` reverses the effect |
| **Configuration revert** | Fast (<2 minutes) | No (hot-reload on next 60s tick) | `settings.yaml` change; restore from backup copy |
| **LKG restore** | Moderate (<5 minutes) | Yes (docker compose build + up) | Code change that cannot be reversed by flag or config |
| **Full revert** | Slow (<15 minutes) | Yes + manual verification | Infrastructure change, Docker configuration, or multi-component failure |

### 8.3 Rollback Decision Matrix

| Trigger | Action | Mechanism |
|---|---|---|
| Health check failure (Step 7) | Immediate rollback | LKG restore |
| Parity check failure (Step 6) | Rebuild container | Return to pipeline Step 4 |
| Container crash within 5 minutes of deployment | Immediate rollback | LKG restore |
| Degraded performance (signals delayed, scoring anomalies) | Disable change | Feature flag toggle to `false` |
| Impossible signal detected | Disable change + investigate | Feature flag toggle to `false`; if no flag, LKG restore |
| Data provider failure post-deploy | Assess causality | If caused by change: LKG restore. If external: data provider failover |

### 8.4 Rollback Authority

**Rollback does NOT require approval.** Any operator who detects a deployment failure or system degradation is authorised and expected to initiate rollback immediately. The principle is: **safety first, review after.**

Post-rollback requirements:
1. Notify PM within 1 hour.
2. File an incident CR (CAT-1) within 24 hours.
3. Root cause analysis within 48 hours.

### 8.5 LKG Restore Procedure

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 << 'ROLLBACK'
cd /home/ubuntu/nzt48-signals

# Step 1: Stop the running container
docker compose down

# Step 2: Identify and checkout the most recent LKG tag
LKG_TAG=$(git tag -l 'lkg-*' --sort=-creatordate | head -1)
echo "Rolling back to: $LKG_TAG"
git stash
git checkout "$LKG_TAG"

# Step 3: Rebuild and restart
docker compose build nzt48
docker compose up -d nzt48

# Step 4: Verify
sleep 10
curl -sf http://localhost:8000/api/health
echo "Rollback complete. Verify system state manually."
ROLLBACK
```

---

## 9. CONFIGURATION CHANGE CONTROL

### 9.1 settings.yaml Changes

All changes to `config/settings.yaml` require:

1. **Diff review.** The exact diff must be reviewed by the approver before the change is applied.
2. **Parameter bounds check.** Every modified parameter must be verified against its documented bounds. If no bounds are documented, the change is escalated to CAT-2 and bounds must be established as part of the CR.
3. **Backup.** A timestamped copy of the previous `settings.yaml` must be preserved:

```bash
cp config/settings.yaml config/settings.yaml.bak.$(date +%Y%m%d-%H%M%S)
```

### 9.2 Feature Flag Toggles

The system contains the following feature flags in `config/settings.yaml`, all defaulting to `false`:

| Flag | Workstream | Controls |
|---|---|---|
| `sanity_gate_v2` | W1 | Premarket sanity gate |
| `leverage_once_assertion` | W2 | Return math leverage validation |
| `provenance_tracking` | W3 | Data provenance tracking |
| `telegram_tape_v2` | W4 | Telegram tape v2 delivery |
| `persistent_dedupe` | W4 | Persistent deduplication |
| `regime_unification` | W5 | Regime detection unification |
| `drought_escalation` | W5 | Drought condition escalation |
| `pdf_qa_gate` | W6 | PDF quality assurance gate |
| `war_room_v2` | W7 | War room v2 interface |
| `datahub_routing` | W8 | Data hub provider routing |
| `universe_governance` | W10 | Universe governance enforcement |
| `learning_loop_hardened` | W11 | Hardened learning loop |
| `always_wired_v1` | W13 | Always-wired connectivity |
| `startup_readiness_gate` | W13 | Startup readiness validation |
| `continuous_integrity` | W13 | Continuous integrity monitoring |
| `self_healing_ops` | W13 | Self-healing operations |

**Rules for flag toggles:**
- Toggling a flag to `true` for the first time requires: the corresponding workstream's acceptance tests to pass, and LE approval.
- Toggling a flag to `false` (disabling) may be done immediately for safety reasons without prior approval (rollback principle).
- Feature flags are hot-reloaded on the next 60-second scan tick. No container restart is required.

### 9.3 Universe Changes

Universe changes (adding, removing, or reclassifying tickers) are governed by the UNIVERSE_GOVERNANCE_PLAN (NZT48-ANNEX-UGP). The CCP adds the following requirements:

1. A CR of category CAT-3 (minimum) must be raised for every universe change.
2. The CR must reference the UNIVERSE_GOVERNANCE_PLAN approval workflow.
3. New tickers must have valid data from the configured data provider before being added.
4. Removed tickers must have no open positions before removal.

### 9.4 Learning Weight Changes

Changes to learning loop weights or adaptive scoring parameters require:

1. A CR of category CAT-3 if the weight change is within 15% of the current value.
2. Escalation to CAT-2 with an IC memo if the weight drift exceeds 15%.
3. The IC memo must document: the current weight, proposed weight, reason for drift, expected impact on signal generation, and backtest validation.

---

## 10. AUDIT TRAIL

### 10.1 CR Log

All Change Requests are logged with the following fields:

| Field | Description |
|---|---|
| CR-ID | Unique identifier |
| Timestamp | ISO 8601 datetime of CR creation |
| Author | Name of the requester |
| Category | CAT-1 through CAT-4 |
| Files Changed | Exact paths of all modified files |
| Approval Chain | Name, date, and decision of each approver |
| Deploy Outcome | DEPLOYED, ROLLED_BACK, or REJECTED |
| LKG Tag | Git tag applied after successful deployment (if applicable) |
| Docker Image Tag | Immutable image tag used for deployment (if applicable) |
| Rollback Events | Timestamp and reason for any rollback (if applicable) |

### 10.2 Authoritative Sources of Truth

| Artefact | Source of Truth |
|---|---|
| Code changes | Git history (commit log, diffs, tags) |
| Configuration changes | Git history + run manifests |
| Feature flag state | `config/settings.yaml` current state + git history for change timeline |
| Deployment events | LKG tags in git + CR log |
| Runtime behaviour | Run manifests (`data/manifests/manifest_YYYYMMDD_HHMMSS.json`) |
| Incident history | CR log (CAT-1 entries) |

### 10.3 Retention

- All CRs are retained indefinitely.
- Git history is never rewritten (`git push --force` is prohibited on `main`).
- Run manifests are retained for a minimum of 12 months.
- LKG tags are never deleted.

---

## 11. ACCEPTANCE TESTS

The following acceptance tests validate that this Change Control Policy is being enforced correctly. These tests MUST pass continuously.

### CCP-T01: CR Without Rollback Plan is Rejected

**Procedure:** Submit a CAT-2 CR with the rollback plan field left blank or marked "N/A".

**Expected Result:** The CR is REJECTED at the review gate. The rejector cites CCP Section 8.1 as the reason.

**Pass Criteria:** CR status is REJECTED. Rejection rationale references the missing rollback plan.

### CCP-T02: Deploy Without Parity Check is Blocked

**Procedure:** Attempt to proceed from deployment pipeline Step 5 (docker compose up) directly to Step 7 (health check), skipping Step 6 (parity check).

**Expected Result:** The deployment is BLOCKED. The pipeline does not proceed without a parity check result.

**Pass Criteria:** No LKG tag is applied. Deployment does not reach Step 9.

### CCP-T03: Rollback Restores LKG State Within 5 Minutes

**Procedure:** Deploy a known-bad change (e.g., a syntax error behind a feature flag). Trigger rollback using the LKG restore procedure (Section 8.5).

**Expected Result:** System returns to the most recent LKG state. `/api/health` returns 200. All critical endpoints respond.

**Pass Criteria:** Total rollback time from initiation to health check pass is less than 5 minutes.

### CCP-T04: Feature Flag Toggle Reverses Change Within 60 Seconds

**Procedure:** Enable a feature flag (set to `true`). Verify the feature is active. Toggle the flag back to `false`.

**Expected Result:** The feature effect is reversed on the next scan cycle (within 60 seconds).

**Pass Criteria:** System behaviour returns to pre-flag state within 60 seconds of the flag toggle.

### CCP-T05: CR Audit Trail Completeness

**Procedure:** Review the last 10 CRs in the CR log.

**Expected Result:** All required fields (per Section 3.2) are populated for every CR. No fields are blank or marked "TBD" in deployed CRs.

**Pass Criteria:** 10 out of 10 CRs have all required fields completed. Zero deployed CRs have incomplete fields.

---

## 12. DEFINITIONS

| Term | Definition |
|---|---|
| **CR** | Change Request. A formal document describing a proposed change to the system. |
| **LKG** | Last Known Good. A git tag marking a system state that has passed all deployment pipeline checks. |
| **Parity Check** | Verification that the code inside the running Docker container matches the code on the EC2 host filesystem. |
| **Blast Radius** | The scope of potential impact if a change causes a failure. Classified as LOW, MEDIUM, HIGH, or CRITICAL. |
| **Feature Flag** | A boolean configuration parameter in `settings.yaml` that enables or disables a specific feature at runtime without requiring a code change or container rebuild. |
| **Run Manifest** | A JSON file produced by each trading session that captures the complete environment state, configuration, and operational metrics for reproducibility. |
| **Smoke Test** | A lightweight post-deployment test that verifies the system completes one full operational cycle without errors. |
| **Promotion Gate** | A set of quantitative criteria that must be met before the system is promoted to a higher-trust environment. |
| **Demotion** | Automatic reversal of a promotion when gate criteria are no longer met. Does not require approval. |
| **Hot Reload** | The ability of the system to pick up configuration changes (including feature flag toggles) on the next 60-second scan tick without a container restart. |

---

## 13. REVIEW SCHEDULE

This policy is a living document subject to periodic review:

| Review Type | Frequency | Reviewer |
|---|---|---|
| Routine review | Quarterly | Lead Engineer + PM |
| Gate criteria review | Before any promotion gate attempt | LE + PM + IC |
| Post-incident review | After any CAT-1 incident | LE + PM (IC if capital impact) |
| Major version review | Before any MAJOR version increment | LE + PM + IC |

Changes to this policy itself require a CAT-2 CR and PM + LE approval.

---

**Document Control**

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-02-27 | Lead Engineer | Initial binding release |

---

*END OF DOCUMENT -- NZT48-ANNEX-CCP-001 v1.0*
