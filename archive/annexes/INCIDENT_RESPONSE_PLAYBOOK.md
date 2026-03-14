# NZT-48 Incident Response Playbook

| Field           | Value                                      |
|-----------------|--------------------------------------------|
| Document ID     | NZT48-ANNEX-IRP-001                        |
| Version         | 1.0                                        |
| Date            | 2026-02-27                                 |
| Status          | **BINDING**                                |
| Classification  | Internal -- Operations                     |
| Owner           | Operator / PM                              |
| Review Cadence  | Quarterly, or after any SEV-1/SEV-2 event  |

---

## TABLE OF CONTENTS

1. [Purpose](#1-purpose)
2. [System Context](#2-system-context)
3. [Severity Levels](#3-severity-levels)
4. [Incident Response Procedure](#4-incident-response-procedure)
5. [Runbooks](#5-runbooks)
6. [Communication Templates](#6-communication-templates)
7. [Evidence Pack](#7-evidence-pack)
8. [Escalation Matrix](#8-escalation-matrix)
9. [Postmortem Template](#9-postmortem-template)
10. [Acceptance Tests](#10-acceptance-tests)
11. [Cross-References](#11-cross-references)
12. [Definitions](#12-definitions)
13. [Revision History](#13-revision-history)

---

## 1. PURPOSE

This playbook establishes the mandatory incident response framework for the NZT-48 Leveraged ISA Intraday Trading System. Its objectives are:

1. **Rapid containment.** Every incident affecting capital, execution integrity, or system availability must be contained within the response time defined for its severity level. In a leveraged trading system, minutes of uncontrolled execution can produce outsized losses.
2. **Evidence preservation.** Before any corrective action is taken, the system state must be captured in an evidence pack. Post-hoc root cause analysis is impossible without contemporaneous records.
3. **Root cause analysis.** Every incident at SEV-1 or SEV-2 must produce a postmortem identifying the root cause, contributing factors, and concrete action items to prevent recurrence.
4. **Continuous learning.** The incident library feeds back into system design, test coverage, monitoring alerts, and this playbook itself. An incident that recurs without an improved response is a governance failure.

**Guiding Principle:** When in doubt, activate the kill switch. A missed trading day costs nothing in paper mode and at most one day's target (2%) in live mode. An uncontained incident in a leveraged system can cost multiples of that in seconds. **Always err on the side of flattening.**

---

## 2. SYSTEM CONTEXT

| Parameter               | Value                                                                    |
|--------------------------|--------------------------------------------------------------------------|
| Mode                     | Paper trading                                                            |
| Starting equity          | £10,000                                                                  |
| EC2 instance             | `54.242.32.11`                                                           |
| Engine container         | `nzt48` (Python trading engine + FastAPI on port 8000)                   |
| Dashboard container      | `nzt48-dashboard` (Next.js on port 3001)                                 |
| Instrument universe      | Leveraged ETPs on London Stock Exchange (3x/5x products)                 |
| Active tickers           | QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L |
| Daily target             | 2% equity growth (compounding)                                          |
| Circuit breaker levels   | L1 = 1.5%, L2 = 2.5%, L3 = 4.0% daily drawdown                         |
| Kill switch methods      | Telegram `/kill`, file trigger `/tmp/nzt48_kill_switch`, API endpoint    |
| Operator model           | Single operator                                                          |

---

## 3. SEVERITY LEVELS

### 3.1 SEV-1 -- CRITICAL

**Response time:** Less than 5 minutes from detection.

**Immediate action:** Activate kill switch FIRST, investigate SECOND. No exceptions.

**Trigger conditions:**

| ID       | Condition                                                         |
|----------|-------------------------------------------------------------------|
| SEV1-01  | System executing trades with incorrect prices or position sizes   |
| SEV1-02  | Kill switch failure -- operator cannot flatten positions           |
| SEV1-03  | Data corruption affecting open position records or equity tracking |
| SEV1-04  | Security breach -- unauthorized access to trading system or EC2   |
| SEV1-05  | Circuit breaker L3 triggered (4.0% daily drawdown)               |
| SEV1-06  | Virtual trader executing trades outside market hours erroneously  |
| SEV1-07  | Position size exceeds maximum allowed by Risk Constitution        |

**Characteristics:** Capital is at risk, execution integrity is compromised, or system security is breached. Every second of delay increases potential damage. The kill switch is the first action, not the last resort.

---

### 3.2 SEV-2 -- HIGH

**Response time:** Less than 30 minutes from detection.

**Immediate action:** Assess whether open positions exist. If yes, evaluate kill switch activation. Halt new entries regardless.

**Trigger conditions:**

| ID       | Condition                                                         |
|----------|-------------------------------------------------------------------|
| SEV2-01  | Signal pipeline producing impossible values (moves > ±30%)       |
| SEV2-02  | Regime contradictions affecting live signal generation            |
| SEV2-03  | Circuit breaker L1 (1.5%) or L2 (2.5%) triggered                |
| SEV2-04  | All data providers down simultaneously                           |
| SEV2-05  | Container crash loop (>3 restarts within 15 minutes)             |
| SEV2-06  | system_state.json corrupted or unreadable                        |
| SEV2-07  | Scoring engine producing identical scores for all tickers        |

**Characteristics:** System integrity is degraded. Signals may be unreliable. No new entries should be taken until the issue is understood and resolved.

---

### 3.3 SEV-3 -- MEDIUM

**Response time:** Less than 2 hours from detection.

**Immediate action:** Log the incident. System remains operational but in degraded mode for the affected component.

**Trigger conditions:**

| ID       | Condition                                                         |
|----------|-------------------------------------------------------------------|
| SEV3-01  | Telegram delivery failures exceeding 30% rate                    |
| SEV3-02  | PDF generation failures (morning or afternoon report)            |
| SEV3-03  | Single data provider down (fallback chain active)                |
| SEV3-04  | Learning engine drift alert triggered                            |
| SEV3-05  | Dashboard / War Room unresponsive                                |
| SEV3-06  | Artifact write failures (non-critical outputs)                   |
| SEV3-07  | Provenance chain broken for non-trading artifacts                |

**Characteristics:** Core trading and risk functions are operational. A supporting subsystem is degraded. The fallback chain or graceful degradation is handling the situation, but the root cause must be addressed to prevent escalation.

---

### 3.4 SEV-4 -- LOW

**Response time:** Less than 24 hours from detection.

**Immediate action:** Log the incident. Address during next maintenance window.

**Trigger conditions:**

| ID       | Condition                                                         |
|----------|-------------------------------------------------------------------|
| SEV4-01  | Cosmetic Telegram formatting issues                              |
| SEV4-02  | Non-critical scheduler job missed (e.g., daily summary)          |
| SEV4-03  | Log rotation failure                                             |
| SEV4-04  | Performance degradation (scan cycle exceeding 60 seconds)        |
| SEV4-05  | Dashboard rendering anomalies (data correct, display incorrect)  |
| SEV4-06  | Stale cache warnings (non-trading data)                          |

**Characteristics:** No impact on trading, risk management, or signal integrity. Cosmetic, performance, or housekeeping issues that should be resolved but do not require immediate attention.

---

### 3.5 Severity Escalation Rules

An incident MUST be escalated to the next severity level if:

- The response time SLA for the current severity is breached without containment.
- The scope of impact widens during investigation (e.g., a single provider outage reveals data corruption).
- The operator determines that the initial severity assessment was too conservative.

An incident MUST NEVER be de-escalated until the root cause is identified and containment is confirmed.

---

## 4. INCIDENT RESPONSE PROCEDURE

### Phase 1 -- DETECT & TRIAGE (0 to 5 minutes)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1.1  | Identify incident source: automated alert, operator observation, scheduled health check, or external report. |
| 1.2  | Record the detection timestamp in UTC.                                 |
| 1.3  | Assign a preliminary severity level using Section 3 trigger conditions.|
| 1.4  | **If SEV-1:** Activate the kill switch IMMEDIATELY (RUNBOOK-001). Do not wait for further analysis. Proceed to Phase 2 with the kill switch active. |
| 1.5  | **If SEV-2:** Determine whether open positions exist. If yes, evaluate whether the issue could cause incorrect exits or P&L miscalculation. If there is any doubt, activate the kill switch. |
| 1.6  | **If SEV-3/4:** Log the incident. No immediate protective action required. |
| 1.7  | Create an incident log entry with: timestamp, source, initial severity, initial description, operator name. |

**Decision tree for kill switch:**

```
Is capital at risk or execution integrity compromised?
├── YES → Activate kill switch immediately (RUNBOOK-001)
├── UNCERTAIN → Activate kill switch (err on the side of safety)
└── NO → Proceed without kill switch, halt new entries if SEV-2
```

---

### Phase 2 -- CONTAIN (5 to 15 minutes)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 2.1  | **SEV-1/2:** Confirm kill switch is active. Verify no new entries are being placed. If kill switch was not activated in Phase 1, activate now for SEV-1. |
| 2.2  | **Preserve evidence BEFORE any corrective action.** Execute the evidence collection script (see Section 7). |
| 2.3  | Capture docker logs: `docker logs nzt48 --tail 500 > /tmp/incident_$(date +%Y%m%d_%H%M%S).log` |
| 2.4  | Copy current `system_state.json`: `cp artifacts/system_state.json /tmp/incident_state_$(date +%Y%m%d_%H%M%S).json` |
| 2.5  | Snapshot the `artifacts/` directory: `tar czf /tmp/incident_artifacts_$(date +%Y%m%d_%H%M%S).tar.gz artifacts/` |
| 2.6  | Take a screenshot of the dashboard current state (if accessible).      |
| 2.7  | **Do NOT restart services until evidence is preserved.** A restart destroys in-memory state and may rotate logs. |
| 2.8  | Send initial communication (Section 6) appropriate to severity level.  |

**Critical rule:** Evidence preservation takes priority over speed of resolution. A 2-minute delay to collect logs is always justified. Restarting without logs means the postmortem will be speculative rather than definitive.

---

### Phase 3 -- INVESTIGATE (15 to 60 minutes)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 3.1  | Review the preserved docker logs for error messages, stack traces, and unusual patterns. |
| 3.2  | Review the latest artifacts (`plays.json`, `signals.json`, `equity_history.json`) for anomalous values. |
| 3.3  | Check Telegram delivery logs for unusual messages or failed deliveries. |
| 3.4  | Check `settings.yaml` for any recent configuration changes (compare with last known good). |
| 3.5  | If data-related: trace the anomalous value back through the pipeline -- data provider response, parsing, scoring, signal generation. |
| 3.6  | If execution-related: review virtual_trader logs, position records, and equity calculations. |
| 3.7  | Identify root cause OR narrow to top 2-3 hypotheses. Document each hypothesis with supporting evidence. |
| 3.8  | If root cause is confirmed: proceed to Phase 4.                        |
| 3.9  | If root cause is uncertain: apply the most conservative containment (keep kill switch active) and continue investigation. |

---

### Phase 4 -- RESOLVE (variable duration)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 4.1  | Determine the fix: code change, configuration change, data correction, or simple restart. |
| 4.2  | Classify the fix per CHANGE_CONTROL_POLICY (NZT48-ANNEX-CCP-001).     |
| 4.3  | **SEV-1 emergency:** CAT-1 emergency deploy is permitted. The change MUST still be logged and receives a mandatory post-hoc review within 24 hours. |
| 4.4  | **SEV-2 and below:** Follow standard change control. CAT-2 (expedited) process is acceptable for SEV-2. |
| 4.5  | Apply the fix in the following order: (a) code/config change, (b) rebuild container if needed, (c) restart container. |
| 4.6  | Verify the fix resolves the issue by reproducing the trigger condition (if safe to do so) or confirming the affected component operates correctly. |
| 4.7  | Re-enable the system: deactivate kill switch only after the fix is verified. |
| 4.8  | Confirm with a full scan cycle that the system produces expected output. |

**Kill switch deactivation checklist:**

- [ ] Root cause identified and fix applied.
- [ ] Fix verified (component operates correctly).
- [ ] No anomalous values in latest scan cycle output.
- [ ] `system_state.json` shows clean state.
- [ ] Operator has actively decided to re-enable (not automated).

---

### Phase 5 -- RECOVER & VERIFY (30 minutes post-fix)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 5.1  | Monitor the system for a minimum of 30 minutes after re-enablement.    |
| 5.2  | Verify all metrics return to normal: scan cycle time, signal count, equity tracking, Telegram delivery. |
| 5.3  | Confirm at least one complete scan cycle produces expected output.      |
| 5.4  | Verify `system_state.json` reflects the correct operational state.     |
| 5.5  | If the issue recurs during the observation window: return to Phase 2 and escalate severity by one level. |
| 5.6  | If stable: mark containment as confirmed in the incident log.          |
| 5.7  | Send resolution communication (Section 6).                             |

---

### Phase 6 -- POSTMORTEM (within 24h for SEV-1/2, within 72h for SEV-3/4)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 6.1  | Complete the postmortem report using the template in Section 9.        |
| 6.2  | Identify concrete, actionable items. Each action item must have an owner and a deadline. |
| 6.3  | Determine whether the incident reveals a gap in: monitoring/alerting, circuit breakers, sanity gates, test coverage, documentation, or this playbook. |
| 6.4  | Update the relevant specifications, runbooks, or governance documents. |
| 6.5  | File the postmortem in the incident library (`annexes/incidents/`).    |
| 6.6  | Send postmortem-available communication (Section 6).                   |
| 6.7  | Schedule follow-up review for action item completion.                  |

---

## 5. RUNBOOKS

### RUNBOOK-001: Kill Switch Activation

**Purpose:** Immediately halt all trading activity and flatten open positions.

**When to use:** Any SEV-1 incident. Any SEV-2 incident where open positions exist and execution integrity is in doubt.

| Step | Action                                                                 | Verification                                          |
|------|------------------------------------------------------------------------|-------------------------------------------------------|
| 1    | **Method A (Telegram):** Send `/kill` command to the NZT-48 Telegram bot. | Bot responds with kill switch confirmation message.   |
| 2    | **Method B (File trigger):** SSH to EC2 and create the trigger file.   | File exists at `/tmp/nzt48_kill_switch`.              |
|      | `ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "touch /tmp/nzt48_kill_switch"` | |
| 3    | **Method C (API):** POST to the kill switch endpoint.                  | Response confirms activation.                         |
|      | `curl -X POST http://54.242.32.11:8000/api/kill_switch -H "Content-Type: application/json" -d '{"action": "activate"}'` | |
| 4    | Verify `system_state.json` shows `kill_switch: true`.                  | `curl http://54.242.32.11:8000/api/health` shows kill switch status. |
| 5    | Verify no new positions are opened after activation.                   | `curl http://54.242.32.11:8000/api/positions` returns no new entries post-activation. |
| 6    | Verify existing positions flattened within 60 seconds (if during market hours). | Positions endpoint shows zero open positions.         |

**Fallback:** If Method A fails, use Method B. If Method B fails, use Method C. If all methods fail, this is itself a SEV-1 incident (SEV1-02). In this case:

1. Stop the container entirely: `docker compose stop nzt48`
2. This is the nuclear option -- it halts all system functions, not just trading.
3. Investigate why all three kill switch methods failed before restarting.

---

### RUNBOOK-002: Emergency Flatten All Positions

**Purpose:** Close all open positions and confirm equity is correctly calculated post-close.

**When to use:** After kill switch activation when positions must be verified as fully closed.

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Activate the kill switch (RUNBOOK-001) if not already active.          |
| 2    | Retrieve current positions: `curl http://54.242.32.11:8000/api/positions` |
| 3    | Verify the response shows no open positions.                           |
| 4    | If positions remain open, close each one manually via the API:         |
|      | `curl -X POST http://54.242.32.11:8000/api/positions/{id}/close`      |
| 5    | After all positions are closed, verify equity is consistent:           |
|      | Check `equity_intraday` in the health endpoint matches expected value. |
| 6    | Cross-reference with `equity_history.json` to confirm no P&L anomalies.|

---

### RUNBOOK-003: Container Crash Recovery

**Purpose:** Diagnose and recover from engine container crashes without losing evidence.

**When to use:** Container has crashed or is in a crash loop (SEV-2 if >3 restarts in 15 min).

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Check recent logs: `docker logs nzt48 --tail 100`                      |
| 2    | If crash loop detected, stop the container to prevent further restarts:|
|      | `docker compose stop nzt48`                                            |
| 3    | Preserve the full crash log:                                           |
|      | `docker logs nzt48 > /tmp/crash_log_$(date +%s).txt 2>&1`             |
| 4    | Check disk space: `df -h`                                              |
| 5    | Check memory usage: `docker stats --no-stream`                         |
| 6    | Check for full `/tmp` or `/var/lib/docker`: these are common causes.   |
| 7    | **If resource exhaustion:** Clean up old artifacts, prune Docker:       |
|      | `docker system prune -f` (removes dangling images/containers only).    |
| 8    | **If code/logic error:** Roll back to Last Known Good (LKG) image:     |
|      | Follow ROLLBACK_PLAN Section 2 (LKG Restore procedure).               |
| 9    | Restart the container: `docker compose up -d nzt48`                    |
| 10   | Verify health: `curl http://54.242.32.11:8000/api/health`             |
| 11   | Monitor for 15 minutes to confirm no further crashes.                  |

---

### RUNBOOK-004: Data Provider Outage

**Purpose:** Confirm graceful degradation is operating correctly when one or more data providers are unavailable.

**When to use:** Logs show data provider errors. SEV-2 if all providers are down; SEV-3 if a single provider is down.

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Identify which provider(s) are down by reviewing engine logs.          |
| 2    | Verify the fallback chain is activating: check provenance records in the latest artifacts for provider source metadata. |
| 3    | **If single provider down:** Confirm fallback is providing data. No action required beyond monitoring. |
| 4    | **If all providers down:** System should auto-degrade to health-only mode (no entries, no signals). Verify this is happening. |
| 5    | Monitor for provider recovery. Most outages are transient (< 30 min).  |
| 6    | If outage exceeds 2 hours: investigate whether the issue is on the provider side or network side (check EC2 outbound connectivity). |
| 7    | No manual data injection. Wait for provider recovery. The system is designed to operate safely with stale or missing data by halting entries. |

---

### RUNBOOK-005: Telegram Delivery Failure

**Purpose:** Diagnose and resolve Telegram message delivery failures.

**When to use:** Telegram delivery rate drops below 70% (SEV-3) or delivery is completely failed.

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Verify bot token validity:                                             |
|      | `curl https://api.telegram.org/bot<TOKEN>/getMe`                       |
| 2    | Check network connectivity from EC2:                                   |
|      | `ping -c 3 api.telegram.org` (from within the container or EC2 host). |
| 3    | Review Telegram delivery logs in docker logs for rate limit errors (HTTP 429). |
| 4    | **If token expired or invalid:** Update the `NZT48_TELEGRAM_TOKEN` environment variable in `docker-compose.yml`, rebuild, and restart. Follow CHANGE_CONTROL_POLICY for the configuration change. |
| 5    | **If rate limited:** Reduce message frequency. The engine has retry with exponential backoff; allow it to recover naturally. |
| 6    | **If network issue:** Check EC2 security group outbound rules. Check AWS status page for regional issues. Wait and monitor. |
| 7    | Telegram is a delivery channel, not a control channel (kill switch excepted). Trading continues unaffected by Telegram outages. |

---

### RUNBOOK-006: Impossible Signal Values

**Purpose:** Contain and investigate signals with values that violate physical or logical constraints.

**When to use:** Signal pipeline produces values exceeding ±30% predicted move, negative prices, NaN/Inf values, or other logically impossible outputs.

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | **DO NOT forward the signal** to any delivery channel. If auto-delivery is active, the sanity gate (SANITY_GATE_SPEC) should have blocked it. If it was not blocked, this is an additional SEV-2 issue (sanity gate failure). |
| 2    | Retrieve the anomalous signal from `plays.json` or `signals.json`.     |
| 3    | Trace the value backwards through the pipeline:                        |
|      | Signal value -> scoring engine -> multiframe analytics -> raw data provider response. |
| 4    | Identify where the impossible value was introduced:                    |
|      | (a) Data provider returned garbage data.                               |
|      | (b) Calculation error in analytics or scoring.                         |
|      | (c) Stale/cached data used where fresh data was expected.              |
| 5    | Check the sanity gate: did it fire? If not, why not? Review `SANITY_GATE_SPEC` thresholds. |
| 6    | **If data provider returned garbage:** Temporarily blacklist the provider for that ticker. File a provider reliability note. |
| 7    | **If sanity gate missed it:** File a bug. Apply an emergency clamp (hardcoded maximum/minimum values) as a CAT-1 change until the gate is fixed. |
| 8    | Verify subsequent scan cycle produces clean values before re-enabling signal delivery. |

---

## 6. COMMUNICATION TEMPLATES

All communications are sent via Telegram to the operator channel. Timestamps are in UTC.

### 6.1 SEV-1 Initial Alert

```
SEV-1 INCIDENT: [title]

Kill switch ACTIVATED. All positions flattened.
Detection time: [HH:MM UTC]
Source: [alert / operator observation / automated check]
Initial assessment: [one sentence]

Investigation in progress. Next update by [HH:MM UTC].
```

### 6.2 SEV-2 Initial Alert

```
SEV-2 INCIDENT: [title]

System in degraded mode. New entries HALTED.
Kill switch: [active / not active -- reason]
Detection time: [HH:MM UTC]
Source: [alert / operator observation / automated check]
Initial assessment: [one sentence]

Investigating. Next update by [HH:MM UTC].
```

### 6.3 SEV-3 Initial Alert

```
SEV-3: [title]

System operational. [component] degraded.
Detection time: [HH:MM UTC]
Impact: [brief description of what is not working]

Investigating. Update when resolved or within [timeframe].
```

### 6.4 SEV-4 Log Entry

```
SEV-4: [title]

Logged for next maintenance window.
Detection time: [HH:MM UTC]
Impact: [none / minimal]
```

### 6.5 Resolution Update

```
RESOLVED: [title]

Root cause: [one sentence]
Fix applied: [one sentence]
System restored at: [HH:MM UTC]
Observation period: [duration] -- stable.

Postmortem to follow within [24h / 72h].
```

### 6.6 Postmortem Available

```
POSTMORTEM: [title]

Filed at: annexes/incidents/[filename]
Action items: [N] identified, [M] assigned.
Review date: [date]
```

---

## 7. EVIDENCE PACK

The following artifacts MUST be collected during Phase 2 (Contain) before any corrective action is taken. A restart, rollback, or code change that occurs before evidence collection renders the postmortem speculative and is a procedural violation.

### 7.1 Required Evidence

| Item | Source | Collection Command / Method |
|------|--------|-----------------------------|
| Docker logs (last 500 lines) | Engine container | `docker logs nzt48 --tail 500 > /tmp/incident_logs_$(date +%s).txt 2>&1` |
| system_state.json | `artifacts/` | `cp artifacts/system_state.json /tmp/incident_state_$(date +%s).json` |
| Artifacts directory snapshot | `artifacts/` | `tar czf /tmp/incident_artifacts_$(date +%s).tar.gz artifacts/` |
| Telegram delivery log (last 50) | Docker logs (filtered) | `docker logs nzt48 --tail 2000 2>&1 \| grep -i telegram > /tmp/incident_telegram_$(date +%s).txt` |
| Dashboard screenshot | Browser | Manual screenshot of War Room / dashboard at time of detection |
| Configuration snapshot | `config/` | `cp config/settings.yaml /tmp/incident_config_$(date +%s).yaml` |
| Operator timeline | Manual | Timestamped notes of all operator actions from detection through containment |

### 7.2 Evidence Retention

- SEV-1/2 evidence: Retained permanently in the incident library.
- SEV-3/4 evidence: Retained for 90 days minimum.
- Evidence MUST NOT be modified after collection. Treat as immutable forensic records.

### 7.3 Quick Collection Script

```bash
#!/bin/bash
# Run from EC2 host during Phase 2 -- Contain
TS=$(date +%Y%m%d_%H%M%S)
INCIDENT_DIR="/tmp/incident_${TS}"
mkdir -p "${INCIDENT_DIR}"

docker logs nzt48 --tail 500 > "${INCIDENT_DIR}/docker_logs.txt" 2>&1
cp artifacts/system_state.json "${INCIDENT_DIR}/system_state.json" 2>/dev/null
tar czf "${INCIDENT_DIR}/artifacts.tar.gz" artifacts/ 2>/dev/null
cp config/settings.yaml "${INCIDENT_DIR}/settings.yaml" 2>/dev/null
docker logs nzt48 --tail 2000 2>&1 | grep -i telegram > "${INCIDENT_DIR}/telegram_logs.txt"

echo "Evidence collected in ${INCIDENT_DIR}"
echo "Incident started at: ${TS}" > "${INCIDENT_DIR}/timeline.txt"
echo "--- Add operator notes below ---" >> "${INCIDENT_DIR}/timeline.txt"
```

---

## 8. ESCALATION MATRIX

### 8.1 Escalation Paths

| Severity | Operator Action | PM Notification | IC Notification |
|----------|-----------------|-----------------|-----------------|
| SEV-1    | Immediate response. Kill switch first. | Within 15 minutes of detection. | Within 1 hour if capital is at risk (live mode) or if kill switch failed. |
| SEV-2    | Immediate response. Assess and contain. | Within 1 hour of detection. | Not required unless escalated to SEV-1. |
| SEV-3    | Respond within SLA. | Informed in daily operational digest. | Not required. |
| SEV-4    | Logged. Resolved in next maintenance window. | Informed in weekly review. | Not required. |

### 8.2 Escalation Triggers

An incident MUST be escalated (operator to PM, or PM to IC) when:

- The response SLA for the current severity level has been breached.
- The root cause cannot be identified within 2x the response SLA.
- The scope of the incident expands beyond the initial assessment.
- The fix requires a change that the operator is not authorized to make unilaterally.
- The incident affects (or could affect) capital in live mode.

### 8.3 Single Operator Considerations

Given the single-operator model:

- If the operator is unavailable during a SEV-1 event, the automated circuit breakers and kill switch file trigger are the only defences. This is an accepted risk in paper mode.
- Before transitioning to live mode, a secondary operator or automated escalation (e.g., PagerDuty, automated kill switch on circuit breaker L3) MUST be in place.
- The operator should ensure Telegram notifications are configured with high-priority alert sounds for SEV-1 and SEV-2 messages.

---

## 9. POSTMORTEM TEMPLATE

Every SEV-1 and SEV-2 incident requires a postmortem. SEV-3 and SEV-4 incidents receive a postmortem at operator discretion or if they recur within 30 days.

```markdown
# Postmortem: [Incident Title]

| Field              | Value                          |
|--------------------|--------------------------------|
| Incident ID        | INC-YYYY-NNN                   |
| Severity           | SEV-[1/2/3/4]                  |
| Date               | YYYY-MM-DD                     |
| Duration           | [detection to resolution]      |
| Author             | [name]                         |
| Status             | [draft / final]                |

## Timeline (UTC)

| Time  | Event                                              |
|-------|----------------------------------------------------|
| HH:MM | [event description]                                |
| HH:MM | [event description]                                |

## Impact

- Positions affected: [count, tickers]
- P&L impact: [amount or "none -- paper mode"]
- System downtime: [duration]
- Missed signals: [count]

## Root Cause

[Detailed technical explanation of what failed and why.]

## Contributing Factors

1. [Factor 1]
2. [Factor 2]

## What Went Well

1. [Thing that worked correctly]
2. [Thing that helped containment]

## What Went Poorly

1. [Thing that failed or was slow]
2. [Gap in monitoring/alerting/process]

## Action Items

| ID   | Action                              | Owner    | Deadline   | Status  |
|------|-------------------------------------|----------|------------|---------|
| AI-1 | [description]                       | [name]   | YYYY-MM-DD | open    |
| AI-2 | [description]                       | [name]   | YYYY-MM-DD | open    |

## Lessons Learned

[What should change in process, code, monitoring, or documentation to prevent recurrence.]

## Documents Updated

- [List any specs, runbooks, or governance docs updated as a result.]
```

---

## 10. ACCEPTANCE TESTS

These tests validate that the incident response capability is operational. They must be executed as drills on the schedule defined in Section 10.2.

### 10.1 Test Definitions

| Test ID | Description                                                        | Pass Criteria                                                    |
|---------|--------------------------------------------------------------------|------------------------------------------------------------------|
| IRP-T01 | Kill switch activation flattens all positions.                     | All open positions closed within 60 seconds of kill switch activation. No new positions opened after activation. |
| IRP-T02 | SEV-1 drill completes full response cycle.                         | All six phases (Detect through Postmortem) completed within 30 minutes (excluding postmortem writing time). Evidence pack is complete. |
| IRP-T03 | Evidence pack collection captures all required items.              | All items from Section 7.1 are present and non-empty. Artifacts are timestamped. No evidence was collected after a restart. |
| IRP-T04 | Postmortem template produces a complete report.                    | All fields in the template (Section 9) are populated. At least one action item is identified. Timeline has minimum 3 entries. |
| IRP-T05 | Escalation notification reaches PM within SLA.                     | SEV-1: PM notified within 15 minutes. SEV-2: PM notified within 1 hour. Notification includes incident summary and current status. |
| IRP-T06 | LKG rollback restores system to operational state.                 | System restored to last known good state within 5 minutes. Health endpoint returns 200. Scan cycle completes successfully. |

### 10.2 Drill Schedule

| Drill Type              | Frequency     | Scope                              |
|-------------------------|---------------|-------------------------------------|
| Kill switch drill       | Monthly       | IRP-T01                            |
| Full SEV-1 simulation   | Quarterly     | IRP-T01 through IRP-T06            |
| Evidence pack drill     | Monthly       | IRP-T03                            |
| LKG rollback drill      | Monthly       | IRP-T06                            |
| Tabletop exercise       | Quarterly     | Walk through a hypothetical SEV-1 scenario without triggering actual actions. Validate decision-making and communication flow. |

### 10.3 Drill Rules

- Drills are conducted in paper mode only.
- Drills must not be conducted during market hours if any live trading is active.
- Drill results are logged in the incident library with `type: drill`.
- A failed drill is treated as a SEV-3 incident (the response capability itself is degraded).

---

## 11. CROSS-REFERENCES

| Document | ID | Relevance |
|----------|----|-----------|
| Risk Constitution | NZT48-ANNEX-RC-001 | Supreme risk authority. Circuit breaker levels, position limits, and kill switch requirements defined here. This playbook operationalises those requirements. |
| Change Control Policy | NZT48-ANNEX-CCP-001 | All fixes applied during Phase 4 (Resolve) must comply with change control. Emergency deploy (CAT-1) is permitted for SEV-1 with post-hoc review. |
| Rollback Plan | NZT48-ANNEX-RP (v2.0) | LKG restore procedure (RUNBOOK-003 step 8) and per-workstream rollback procedures. |
| Self-Healing Ops Spec | NZT48-ANNEX-SHO-001 | Defines the boundary between automated recovery and human-required intervention. Self-healing actions may resolve SEV-3/4 incidents without operator action. |
| Sanity Gate Spec | NZT48-ANNEX-SG | Defines thresholds for impossible values. RUNBOOK-006 depends on sanity gates functioning correctly. |
| Forensics Map | NZT48-ANNEX-FM | Maps all evidence sources and their locations. Supports evidence collection in Phase 2. |
| Provenance Spec | NZT48-ANNEX-PROV | Data lineage tracking. Used during Phase 3 investigation to trace anomalous values to their source. |

---

## 12. DEFINITIONS

| Term | Definition |
|------|------------|
| **Kill switch** | A mechanism to immediately halt all trading activity and flatten open positions. Available via Telegram command, file trigger, or API endpoint. |
| **Flatten** | Close all open positions at current market price. In paper mode, this updates the virtual trader's position and equity records. |
| **Circuit breaker** | Automated daily drawdown limit that triggers protective action. L1 (1.5%) = warning + reduced sizing. L2 (2.5%) = halt new entries. L3 (4.0%) = kill switch activation. |
| **LKG (Last Known Good)** | The most recent system state (code, configuration, and data) that passed all health checks and produced correct output. Tagged in git. |
| **Evidence pack** | The collection of logs, state files, artifacts, and operator notes preserved during an incident for postmortem analysis. |
| **Postmortem** | A structured after-action review documenting the timeline, root cause, impact, and corrective actions for an incident. |
| **CAT-1 change** | An emergency change category (per CHANGE_CONTROL_POLICY) that permits immediate deployment with post-hoc review. Reserved for SEV-1 incidents. |
| **Scan cycle** | One complete execution of the engine's main loop: data fetch, analysis, scoring, signal generation, and delivery. Nominally 60 seconds. |
| **Crash loop** | A container that restarts more than 3 times within 15 minutes, indicating a persistent failure that restart alone cannot resolve. |
| **PM** | Project Manager / Portfolio Manager. First escalation point for SEV-1 and SEV-2 incidents. |
| **IC** | Investment Committee. Second escalation point for SEV-1 incidents involving capital risk. |

---

## 13. REVISION HISTORY

| Version | Date       | Author    | Changes                      |
|---------|------------|-----------|------------------------------|
| 1.0     | 2026-02-27 | Operator  | Initial release. All sections drafted and reviewed. |

---

*This document is a binding operational instrument for the NZT-48 trading system. Non-compliance with any mandatory provision during an active incident constitutes a procedural violation subject to postmortem review. This playbook must be reviewed and updated after every SEV-1 or SEV-2 incident, and on the quarterly review schedule.*
