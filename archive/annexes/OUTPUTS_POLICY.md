# NZT-48 Outputs Policy -- Governance Layer

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-OP-001             |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- IC/PM Governance   |
| Related         | OUTPUT_POLICY_SPEC.md (NZT-OPS-002) -- technical gate specification |

---

## 1. PURPOSE

This document governs the **operational policy** for all NZT-48 output channels. While OUTPUT_POLICY_SPEC.md defines the 8 technical fail-closed gates (staleness, magnitude, consistency, score floor, rate governance, restart hygiene, kill switch persistence, and the fail-closed default), this document addresses the governance layer that sits above those gates:

- Per-channel governance rules and accountability
- Deduplication governance and persistence requirements
- Restart hygiene from an operator-readiness perspective
- The fail-closed default as organisational policy (not just code behaviour)
- Output audit trail requirements for IC/PM review
- Operator override governance and approval chain

**Relationship to OUTPUT_POLICY_SPEC.md:** This document does not duplicate the technical specifications. It defines the governance framework within which those specifications operate. Where this document and OUTPUT_POLICY_SPEC.md overlap, the stricter rule applies.

---

## 2. OUTPUT CHANNELS

### 2.1 Telegram

| Governance Rule | Specification |
|-----------------|---------------|
| **Accountability** | Every Telegram message is attributable to a specific scan cycle (`run_id`), strategy, and gate evaluation chain |
| **Content Classification** | Messages MUST carry a type label: `[SIGNAL]`, `[BRIEF]`, `[SYSTEM]`, `[ALERT]`, `[REGIME]`, `[ERROR]`, `[CRITICAL]` |
| **Retention** | All sent and suppressed messages logged to `data/telegram_debug.jsonl` with 30-day retention |
| **Mode Filtering** | In DEGRADED mode: only `[SYSTEM]` and `[CRITICAL]` messages sent. In HALTED mode: only `[CRITICAL]` messages sent |
| **Recipient Control** | Single operator chat ID; no broadcast channels without explicit IC approval |
| **Delivery Confirmation** | Telegram API response status logged; failed sends trigger retry (max 3) then suppression with `[SYSTEM]` alert |
| **No Unsupervised Expansion** | New message types require documented approval in DECISION_REGISTER.md before implementation |

### 2.2 PDF Reports

| Governance Rule | Specification |
|-----------------|---------------|
| **Accountability** | Every PDF carries a generation timestamp, system version, config hash, and QA gate result in its header |
| **Pre-Send QA Gate** | No PDF is delivered without passing the 7-check QA gate (see PDF_DESK_NOTES_SPEC.md). Failed QA produces DRAFT watermark |
| **Lane Separation** | PDF1 (Momentum) MUST NOT contain risk content. PDF2 (Risk) MUST NOT contain entry recommendations. Violations are QA gate failures |
| **Schedule Adherence** | PDFs generate at fixed times (P5=06:30, P1=07:00, P2=13:30, P6=16:40, P3=22:00, P4=22:30, P7=00:00 UK). Deviations logged as incidents |
| **Archive Requirement** | Every PDF archived to `data/reports/` with timestamped filename. No PDF is generated and discarded without archival |
| **Version Control** | PDF generators are versioned. Any change to a PDF generator requires QA gate re-validation |

### 2.3 War Room Dashboard

| Governance Rule | Specification |
|-----------------|---------------|
| **Accountability** | Every data point displayed traces back to an artifact file or API endpoint with provenance |
| **Real-Time Requirement** | Signal Feed, Virtual Positions, and Scan Health update via WebSocket push (target: <5s latency). All other panels poll at documented intervals |
| **Stale Data Handling** | Panels MUST display freshness indicators. Data older than 5 minutes MUST show red "STALE" warning. No silent staleness |
| **Error Transparency** | Panel errors display the error state (grey overlay, error message, retry button). Panels MUST NOT show stale data without visual indication |
| **Access Control** | State-mutating endpoints (kill, pause, resume) require API key authentication. Read endpoints restricted to localhost |
| **No Silent Degradation** | If the War Room API is down, the engine continues operating. War Room is a monitoring channel, not a control dependency |

---

## 3. DEDUPLICATION GOVERNANCE

### 3.1 Dedupe Windows Per Message Type

| Message Type | Dedupe Window | Hash Components | Rationale |
|-------------|---------------|-----------------|-----------|
| `[SIGNAL]` | 5 minutes | ticker + direction + strategy + score_bucket | Prevents duplicate signal delivery within a scan cycle and one retry |
| `[BRIEF]` | 60 minutes | brief_type + session_date | One premarket brief per session |
| `[REGIME]` | 10 minutes | regime_state + layer | Regime changes are infrequent; 10 min prevents transition-chatter |
| `[ALERT]` | 15 minutes | alert_type + ticker | Prevents alert spam for sustained conditions |
| `[SYSTEM]` | 5 minutes | message_hash | System messages may repeat on restart; short window |
| `[ERROR]` | 30 minutes | error_type + module | Error conditions tend to persist; longer window reduces noise |
| `[CRITICAL]` | 0 (no dedupe) | N/A | Critical messages are always delivered. Operator must see every one |

### 3.2 Hash Persistence

| Requirement | Specification |
|-------------|---------------|
| **Storage** | Dedupe hashes persisted to SQLite `telegram_state` table (feature flag: `persistent_dedupe`) |
| **Schema** | `content_hash TEXT, message_type TEXT, created_at TIMESTAMP, expires_at TIMESTAMP` |
| **Cleanup** | Expired hashes purged every 60 minutes by background job |
| **Capacity** | Maximum 10,000 active hashes. If exceeded, oldest expired first (LRU eviction) |
| **Fallback** | If DB write fails, fall back to in-memory dedupe with degradation logged |

### 3.3 Restart Behaviour

On engine restart, the dedupe subsystem MUST:

1. **Load** all unexpired hashes from the `telegram_state` table.
2. **Verify** hash count and integrity (corrupted entries discarded with warning).
3. **Resume** deduplication using persistent state -- no "clean slate" that allows duplicates.
4. **Log** the number of restored hashes at startup: `"Dedupe restored: {N} active hashes"`.

If the `persistent_dedupe` feature flag is disabled, dedupe reverts to in-memory-only operation. On restart, the dedupe window is empty, and duplicate signals within the first window duration are possible. This is a known, documented, and accepted limitation when the flag is off.

---

## 4. RESTART HYGIENE

### 4.1 5-Minute Quiet Mode

On every engine restart, the system enters a 5-minute QUIET MODE. During this period:

| Channel | Behaviour |
|---------|-----------|
| Telegram signals | **DEFERRED** -- queued for delivery after quiet mode ends |
| Telegram system messages | **ALLOWED** -- operator needs restart visibility |
| PDF generation | **DEFERRED** -- if scheduled during quiet mode, delayed to quiet mode end |
| War Room | **ACTIVE** with `SYSTEM RESTARTING` banner |
| Kill switch | **RESTORED** from persistent state before any other action |

### 4.2 Deferred Message Queue

Messages deferred during quiet mode are held in a priority queue:

| Priority | Sorting | Behaviour |
|----------|---------|-----------|
| High | `[CRITICAL]` messages | Delivered immediately after quiet mode ends |
| Medium | `[SIGNAL]` messages sorted by composite score (highest first) | Re-evaluated against ALL gates before delivery |
| Low | `[BRIEF]`, `[REGIME]`, `[ALERT]` messages | Re-evaluated; sent if still valid |

**Re-evaluation rule:** Every deferred message passes through the full gate chain (staleness, consistency, score floor, rate) on exit from quiet mode. Messages that became stale during the 5-minute window are discarded. This prevents the "restart flood" anti-pattern where the system sends a burst of outdated signals.

### 4.3 State Recovery Checklist

Before quiet mode ends, the engine MUST have completed:

| Step | Description | Failure Action |
|------|-------------|---------------|
| 1 | Restore kill switch / pause state from `nzt48_output_state.json` | If state file corrupt: activate kill switch (fail-closed) |
| 2 | Re-establish data feed connections | If feeds down: enter DEGRADED mode |
| 3 | Run at least one complete scoring cycle | If scoring fails: suppress all signals |
| 4 | Verify regime classification is current | If regime stale: suppress signals until fresh classification |
| 5 | Restore dedupe hashes from persistence | If DB unavailable: log warning, proceed with in-memory |

---

## 5. FAIL-CLOSED DEFAULT

### 5.1 Organisational Policy Statement

**The NZT-48 system is fail-closed by design and by governance policy.** When any component is uncertain about data quality, signal validity, system state, or delivery safety, the system suppresses output rather than sending potentially harmful information.

This policy is not merely a technical implementation detail. It is an organisational commitment:

- A missed signal costs opportunity.
- A bad signal costs capital.
- The asymmetry is absolute.

### 5.2 Fail-Closed Scope

| Condition | Output Behaviour |
|-----------|-----------------|
| Any data quality check fails | Telegram: SUPPRESS. PDF: DRAFT watermark. War Room: DEGRADED banner |
| Gate evaluation itself throws an exception | Telegram: SUPPRESS. Treat as gate failure, not gate pass |
| Data field has no timestamp | Treat as infinitely stale. BLOCK the message |
| Score is null or unparseable | Treat as score 0. SUPPRESS on all channels |
| Regime engine returns null | BLOCK all signals until regime is resolved |
| Dedupe subsystem unavailable | ALLOW messages (dedupe failure is less dangerous than suppressing all output) |
| Rate limiter state unknown | Apply maximum rate limit (most conservative setting) |
| Kill switch state file corrupt | Activate kill switch (fail-closed). Notify operator |
| Unknown or unhandled condition | SUPPRESS. Log at CRITICAL level. Notify operator |

### 5.3 Exception: Monitoring Channels

The following channels are exempt from fail-closed suppression because they serve diagnostic purposes:

- Internal logging (stdout, Docker logs, file logs) -- always active
- Health check endpoints (`/health`, `/status`) -- always respond
- Metrics/monitoring endpoints -- always respond
- War Room data display (with appropriate banners/watermarks) -- always active

---

## 6. OUTPUT AUDIT TRAIL

### 6.1 Per-Message Audit Record

Every outbound message (sent, suppressed, deferred, or blocked) MUST generate an audit record with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO-8601 UTC | When the output decision was made |
| `message_type` | enum | SIGNAL, BRIEF, SYSTEM, ALERT, REGIME, ERROR, CRITICAL |
| `channel` | enum | TELEGRAM, PDF, WARROOM |
| `content_hash` | SHA-256 (first 12 chars) | Hash of message content for deduplication and traceability |
| `gate_results` | object | Per-gate PASS/BLOCK/SUPPRESS/HOLD/DEFER verdict |
| `delivery_status` | enum | SENT, SUPPRESSED, BLOCKED, DEFERRED, HELD, FAILED |
| `run_id` | UUID | Engine scan cycle that generated this output |
| `signal_id` | string or null | Signal identifier if message is a signal |
| `ticker` | string or null | Ticker symbol if applicable |
| `reason` | string | Human-readable reason for the delivery status |

### 6.2 Audit Log Locations

| Log | Path | Format | Retention |
|-----|------|--------|-----------|
| Gate decisions | `/data/logs/output_policy.log` | Structured JSON, one entry per line | 30 days |
| Override audit | `/data/logs/override_audit.log` | Structured JSON, append-only | 90 days (compliance) |
| Telegram debug | `/data/telegram_debug.jsonl` | JSONL | 30 days |
| PDF QA log | `/data/pdf_qa_log.jsonl` | JSONL | 30 days |
| Cross-PDF audit | `/data/pdf_cross_audit.jsonl` | JSONL | 30 days |

### 6.3 Audit Integrity

- Override audit logs are **append-only**. No deletion or modification is permitted.
- All audit logs include a `sequence_number` field that increments monotonically. Gaps in the sequence indicate log corruption or tampering.
- Audit logs are included in the daily parity check (Section 5.2 of EC2_DOCKER_DRIFT_GUARDS.md).

---

## 7. OPERATOR OVERRIDES

### 7.1 Override Governance

Operator overrides allow temporary bypass of output gates under controlled conditions. Every override is:

- **Authenticated**: Requires API key (`NZT_OVERRIDE_API_KEY`, 256-bit).
- **Reason-documented**: Mandatory reason field (minimum 10 characters).
- **Time-limited**: Default 300 seconds (5 minutes). Maximum 3600 seconds (1 hour).
- **Audited**: Logged to `override_audit.log` with full context (see OUTPUT_POLICY_SPEC.md Section 13).
- **Non-stacking**: A new override replaces the previous one for the same gate.

### 7.2 Override Approval Matrix

| Gate Override | Self-Approval (Operator) | Requires PM Awareness | Requires IC Approval |
|--------------|--------------------------|----------------------|---------------------|
| Quality bypass (R1) | YES (max 5 min) | YES (notify within 1 hour) | NO |
| Staleness bypass (R2) | YES (max 5 min) | NO | NO |
| Magnitude release (R3) | YES (per signal) | YES (if >3 releases/day) | NO |
| Regime bypass (R4) | YES (max 5 min) | YES (notify immediately) | NO |
| Score floor change (R5) | YES (max 5 min) | YES (notify within 1 hour) | YES (if lowered below 5) |
| Rate reset (R6) | YES | NO | NO |
| Kill switch toggle | YES | YES (notify immediately) | NO (emergency authority) |
| Kill switch deactivation after auto-trigger | YES | YES (notify within 30 min) | NO |

### 7.3 Override Escalation

If an operator uses more than **5 overrides in a single trading session**, this constitutes an escalation event:

1. A `[SYSTEM] OVERRIDE_ESCALATION` message is sent to Telegram.
2. The operator MUST document the pattern in the session's incident log.
3. PM reviews the override pattern within 24 hours.
4. If the pattern indicates a systematic gate miscalibration, a CHANGE_CONTROL_POLICY.md change request is required.

---

## 8. ACCEPTANCE TESTS

| Test ID | Scenario | Expected Result | Pass Criteria |
|---------|----------|-----------------|---------------|
| OP-T01 | Send a signal through all gates; verify audit record created | Audit log entry with all required fields | Entry in `output_policy.log` with correct `gate_results`, `delivery_status`, and `content_hash` |
| OP-T02 | Suppress a signal due to quality gate failure; verify suppression audit | Audit log shows SUPPRESSED with quality gate as failing gate | `delivery_status=SUPPRESSED`, `gate_results.fail_closed=BLOCK` |
| OP-T03 | Restart engine; verify quiet mode activates and deferred messages re-evaluated | No Telegram signals during 5-min window; deferred signals re-checked after | Zero outbound Telegram during quiet mode; stale deferred signals discarded |
| OP-T04 | Send duplicate signal within dedupe window; verify only one delivered | Second signal suppressed by dedupe | `delivery_status=SUPPRESSED`, `reason=DEDUPE_HIT` |
| OP-T05 | Override quality gate; verify override logged with reason | Override audit entry with all fields | `override_audit.log` entry with `gate=quality`, `reason` (min 10 chars), `duration_seconds` |
| OP-T06 | Trigger 6 overrides in one session; verify escalation alert | `[SYSTEM] OVERRIDE_ESCALATION` message sent | Telegram receives escalation notification |
| OP-T07 | Corrupt kill switch state file; restart engine; verify fail-closed activation | Kill switch activates on restart; operator notified | Kill switch ACTIVE; `[CRITICAL]` message sent to Telegram |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial governance specification |

---

*End of Document NZT48-ANNEX-OP-001*
