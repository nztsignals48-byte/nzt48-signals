# NZT-48 Postmortem Library -- Template and Process

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-PLT-001            |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- Operations         |

---

## 1. PURPOSE

Define the standard template, filing process, and review cadence for all NZT-48 system incidents. Every incident that impacts signal delivery, data quality, system uptime, or capital (in live mode) must produce a postmortem. The goal is continuous improvement through blameless analysis of system failures.

---

## 2. POSTMORTEM TEMPLATE

Every postmortem follows this exact structure. All fields are mandatory unless marked optional.

```markdown
# Postmortem: [PM-ID] [Title]

## Metadata

| Field              | Value                          |
|--------------------|--------------------------------|
| PM-ID              | PM-YYYYMMDD-NNN                |
| Title              | [Short descriptive title]      |
| Date of Incident   | YYYY-MM-DD                     |
| Severity           | SEV-1 / SEV-2 / SEV-3 / SEV-4 |
| Duration           | [Total time from detection to resolution] |
| Impact             | [What was affected and how]    |
| Author             | [Name]                         |
| Reviewers          | [Names]                        |
| Status             | DRAFT / REVIEWED / CLOSED      |

## Timeline of Events

| Time (UTC) | Event | Source |
|------------|-------|--------|
| HH:MM | [What happened] | [How it was detected: log, alert, manual observation] |
| HH:MM | [Next event] | [Source] |
| ... | ... | ... |

## Root Cause

[Detailed technical explanation of why the incident occurred.
Be specific. Reference code files, line numbers, configuration values.
One paragraph minimum.]

## Contributing Factors

- [Factor 1: e.g., "No staleness check on premarket data"]
- [Factor 2: e.g., "yfinance returned cached pre-split price"]
- [Factor 3: e.g., "Magnitude gate not yet implemented"]

## What Went Well

- [Positive 1: e.g., "Kill switch activated within 30 seconds"]
- [Positive 2: e.g., "Operator detected anomaly before any trade was placed"]

## What Went Wrong

- [Problem 1: e.g., "Impossible percentage value reached Telegram"]
- [Problem 2: e.g., "No automated detection of implausible values"]

## Action Items

| # | Action | Owner | Due Date | Status |
|---|--------|-------|----------|--------|
| 1 | [Specific action] | [Name] | YYYY-MM-DD | OPEN / IN PROGRESS / DONE |
| 2 | [Specific action] | [Name] | YYYY-MM-DD | OPEN / IN PROGRESS / DONE |

## Lessons Learned

- [Lesson 1: What the team/operator should remember]
- [Lesson 2: What systemic change this motivates]

## Related Incidents

- [PM-ID of related incidents, if any]
- [Link to relevant annex or spec document]
```

---

## 3. SEVERITY CLASSIFICATION

| Severity | Definition | Examples | Review Timeline |
|----------|-----------|----------|----------------|
| **SEV-1** | Capital at risk (live mode), data corruption, or impossible signals sent to operator | Impossible premarket percentages delivered to Telegram; virtual trader executes at corrupt price; kill switch fails to activate | PM review within 24 hours; IC notification within 48 hours |
| **SEV-2** | System-wide outage or degradation affecting all output channels during market hours | Engine crash loop; all data feeds down; Telegram delivery failure for >30 minutes; Docker container won't start | PM review within 48 hours |
| **SEV-3** | Single subsystem failure with limited impact | One PDF type fails QA; one API endpoint returns 500; dedupe not working; single ticker data missing | Lead engineer review within 1 week |
| **SEV-4** | Minor issue with no operator impact | Cosmetic War Room bug; log formatting error; non-critical test failure; configuration drift detected and auto-corrected | Review at next weekly ops meeting |

---

## 4. INDEXING RULES

### 4.1 File Naming

```
incidents/PM-YYYYMMDD-NNN.md
```

Where:
- `YYYYMMDD` = date of incident (not date of postmortem writing)
- `NNN` = sequential number for that day (001, 002, etc.)

Examples:
- `incidents/PM-20260227-001.md` -- first incident on 27 Feb 2026
- `incidents/PM-20260227-002.md` -- second incident on same day

### 4.2 Storage

All postmortems stored in `incidents/` directory at the project root.

### 4.3 Index File

An index file `incidents/INDEX.md` maintains a chronological list of all postmortems:

```markdown
# NZT-48 Incident Index

| PM-ID | Date | Severity | Title | Status |
|-------|------|----------|-------|--------|
| PM-20260227-001 | 2026-02-27 | SEV-1 | Impossible premarket percentage in brief | CLOSED |
| PM-20260228-001 | 2026-02-28 | SEV-3 | PDF1 count integrity mismatch | REVIEWED |
```

---

## 5. REVIEW PROCESS

### 5.1 Immediate Response

1. **Detect** the incident (automated alert, manual observation, or operator report).
2. **Mitigate** the impact (kill switch, feature flag toggle, LKG restore -- as appropriate).
3. **Log** the incident in `data/INCIDENT_LOG.jsonl` with timestamp, action, reason (see ROLLBACK_PLAN.md Section 4.4).

### 5.2 Postmortem Writing

1. **Within 24 hours** (SEV-1/2) or **within 1 week** (SEV-3/4): draft the postmortem using the template above.
2. Fill in all mandatory fields. Be specific about root cause and timeline.
3. Propose at least 2 action items with owners and due dates.

### 5.3 Review

| Severity | Reviewer | Review Deadline |
|----------|---------|----------------|
| SEV-1 | PM + IC member | 24 hours after draft |
| SEV-2 | PM | 48 hours after draft |
| SEV-3 | Lead engineer | 1 week after draft |
| SEV-4 | Self-review sufficient | Next weekly ops meeting |

### 5.4 Closure

A postmortem is CLOSED when:
- All action items are DONE or explicitly deprioritised with documented reason.
- The fix has been deployed and verified.
- The reviewer has signed off.

---

## 6. BLAMELESS CULTURE

### 6.1 Principles

- **Focus on systems, not individuals.** The question is "why did the system allow this?" not "who caused this?"
- **Every incident is a learning opportunity.** The goal is to prevent recurrence, not to assign blame.
- **Assume positive intent.** Operators make decisions with the information available at the time.
- **Share openly.** Postmortems are shared with all stakeholders. Hiding incidents guarantees they will recur.

### 6.2 Language Guidelines

| Avoid | Use Instead |
|-------|-------------|
| "The operator failed to..." | "The system did not alert the operator to..." |
| "This was a careless mistake" | "The process lacked a check for this condition" |
| "Someone should have noticed" | "The monitoring did not surface this condition" |
| "This was human error" | "The system design assumed X, which was not valid in this case" |

---

## 7. EXAMPLE POSTMORTEM

```markdown
# Postmortem: PM-20260227-001 Impossible Premarket Percentage in Brief

## Metadata

| Field              | Value                                    |
|--------------------|------------------------------------------|
| PM-ID              | PM-20260227-001                          |
| Title              | Impossible premarket percentage in brief |
| Date of Incident   | 2026-02-27                               |
| Severity           | SEV-1                                    |
| Duration           | 12 minutes (detection to resolution)     |
| Impact             | Impossible +340% overnight return for QQQ3.L sent to Telegram premarket brief |
| Author             | rr                                       |
| Reviewers          | --                                       |
| Status             | CLOSED                                   |

## Timeline of Events

| Time (UTC) | Event | Source |
|------------|-------|--------|
| 06:31 | Premarket brief generated with QQQ3.L showing +340% overnight return | Automated PDF + Telegram delivery |
| 06:32 | Operator sees impossible value in Telegram notification | Manual observation |
| 06:33 | Operator activates kill switch via `/kill ALL` | Telegram command |
| 06:35 | Investigation begins: check yfinance data for QQQ3.L | Manual SSH + Python REPL |
| 06:38 | Root cause identified: yfinance returned stale pre-stock-split close price as previous close | yfinance debug |
| 06:40 | Feature flag `sanity_gate_v2` noted as not yet implemented | Code review |
| 06:43 | Kill switch deactivated after confirming no trades were placed | Telegram `/resume ALL` |

## Root Cause

yfinance returned a cached previous close price for QQQ3.L that predated a
recent stock split. The current close was post-split (e.g., 85.00) while the
previous close was pre-split (e.g., 340.00). The return calculation
`(85 - 340) / 340 = -75%` should have triggered, but due to the data being
cached in the wrong direction, the system computed `(340 - 85) / 85 = +300%`.

No magnitude validation existed at the time to catch this implausible value.

## Contributing Factors

- No magnitude filter on premarket data (sanity_gate_v2 not yet implemented)
- yfinance does not reliably adjust for corporate actions in all cases
- No staleness check on the previous close timestamp
- Premarket brief generation path bypassed signal qualification gates

## What Went Well

- Operator detected the impossible value within 1 minute of delivery
- Kill switch activated within 2 minutes
- No trades were placed (paper mode, no virtual trader triggered by briefs)
- Incident logged immediately

## What Went Wrong

- Impossible value reached Telegram without any automated check
- The premarket brief path has no magnitude gate
- yfinance data was not validated for corporate actions
- No automated alert for statistically implausible values

## Action Items

| # | Action | Owner | Due Date | Status |
|---|--------|-------|----------|--------|
| 1 | Implement magnitude gate (W1 sanity_gate_v2) for all output paths including premarket briefs | rr | 2026-03-01 | DONE |
| 2 | Add corporate action detection for .L tickers | rr | 2026-03-15 | OPEN |
| 3 | Add staleness check on previous close timestamp | rr | 2026-03-01 | DONE |
| 4 | Ensure premarket brief path passes through same gates as signal path | rr | 2026-03-01 | DONE |

## Lessons Learned

- Every output path needs the same quality gates, not just the signal path.
  Briefs, alerts, and system messages can all contain data that should be
  validated before delivery.
- Free data providers (yfinance) have data quality gaps that are silent and
  dangerous. Migration to a commercial provider is essential before live trading.
- The fail-closed principle should extend to data validation: if a value is
  statistically implausible, suppress it and alert the operator.

## Related Incidents

- None (first recorded incident)
- Related spec: OUTPUT_POLICY_SPEC.md Rule 3 (Magnitude Gate)
- Related workstream: W1 (Premarket Sanity)
```

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial template and process |

---

*End of Document NZT48-ANNEX-PLT-001*
