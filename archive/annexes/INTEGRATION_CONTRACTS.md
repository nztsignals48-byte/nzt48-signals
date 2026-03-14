# INTEGRATION CONTRACTS -- BINDING SPECIFICATION

**Document ID**: NZT48-ANNEX-IC-001
**Version**: 1.0
**Date**: 2026-02-27
**Status**: BINDING -- All cross-component wiring MUST conform to this specification
**Scope**: System-of-record objects, artifact schemas, cross-component invariants, interface contracts, failure modes, and operator actions for the NZT-48 trading system
**Traceability**: References FORENSICS_MAP.md, OPS_GOVERNANCE_PLAN.md, PROVENANCE_SPEC.md, REGIME_DROUGHT_SPEC.md, TELEGRAM_TAPE_SPEC.md, OUTPUT_POLICY_SPEC.md, WAR_ROOM_REQUIREMENTS_SPEC.md

---

## 1. OBJECTIVE

Define the formal integration contracts between all NZT-48 system components so that wiring drift is detectable and components cannot silently diverge.

The NZT-48 system is a distributed pipeline: the trading engine (`main.py`) produces artifacts; the War Room API (`FastAPI :8000`), Telegram pipeline (`telegram_bot.py`), and PDF generator (`pdf_v2_momentum.py`, `pdf_v2_risk.py`) consume those artifacts. The Learning Loop reads signal outcomes and writes to the Edge Ledger. The Provenance Engine tags every data point with origin metadata.

Without formal integration contracts:
1. A module may compute its own regime label, diverging from the canonical source.
2. An artifact schema may drift between writer and reader, producing silent data loss.
3. A restart may lose in-memory state (kill switch, dedupe window, drought tier) that consumers assume is persistent.
4. Timestamps across related outputs may diverge beyond acceptable tolerance, producing contradictory reports.

This document eliminates those failure modes by defining ONE authoritative source for each system object, ONE canonical schema for each artifact, and enforceable invariants across all cross-component boundaries.

**Design Principle**: Fail-closed. If a contract is violated, the affected output channel is suppressed until the violation is resolved. A missed signal costs opportunity; a corrupted signal costs capital and trust.

---

## 2. SYSTEM-OF-RECORD OBJECTS (SINGULAR SOURCES OF TRUTH)

Each object below has ONE and ONLY ONE authoritative source. No component may compute, derive, or cache its own version of these objects for output purposes.

### 2.1 SystemState

| Property | Value |
|----------|-------|
| **Authoritative Source** | `artifacts/system_state.json` |
| **Writer** | `command_center/state.py` (sole writer) |
| **Readers** | War Room API, Telegram pipeline, PDF generator, Go-Live Gate, Continuous Integrity Monitor |
| **Refresh Cadence** | Written on every scan tick (60s) |
| **Persistence** | File-based; survives engine restart |

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `mode` | `enum: PAPER, LIVE, BACKTEST` | Current trading mode |
| `regime` | `string` | Canonical 8-state market regime label (from `regime_mapping.py`) |
| `data_reliability` | `float [0.0-1.0]` | Overall data health score from provenance engine |
| `signals_emitted` | `int` | Total signals emitted this session |
| `kill_switch` | `bool` | Whether the kill switch is engaged |
| `drought_state` | `object` | Current drought tier and metadata (see Section 2.4 cross-ref) |
| `engine_runs` | `int` | Number of scan cycles completed this session |
| `last_heartbeat_utc` | `ISO8601` | Timestamp of most recent scan tick |
| `run_id` | `string (uuid-v4)` | Session identifier, propagated to all outputs |
| `config_hash` | `string (sha256)` | Hash of `config/settings.yaml` at session start |
| `startup_gate_passed` | `bool` | Whether the startup readiness gate has passed |

**Invariant**: No component may compute its own system state independently. All consumers read from this single artifact. Any module that needs system state MUST import it from `artifacts/system_state.json` or the in-memory cache refreshed from that file.

### 2.2 RegimeState

| Property | Value |
|----------|-------|
| **Authoritative Source** | `volatility_regime.py` (5-state internal) -> `core/regime_mapping.py` (canonical mapping) -> stored in `system_state.json:regime` |
| **Writer** | Volatility regime classifier (5-state per-ticker) -> Canonical mapper (to 8-state market-wide for bot-facing output) |
| **Readers** | Telegram (regime labels), PDF (regime sections), War Room (regime badge), Drought manager (contradiction detection) |
| **Refresh Cadence** | Every scan tick (60s) |

**Dual-Layer Model (per REGIME_DROUGHT_SPEC):**

| Layer | Taxonomy | States | Scope | Usage |
|-------|----------|--------|-------|-------|
| Layer 1: Market Regime | Taxonomy B (8-state) | TRENDING_UP_STRONG, TRENDING_UP_MOD, TRENDING_DOWN_STRONG, TRENDING_DOWN_MOD, RANGE_BOUND, HIGH_VOLATILITY, RISK_OFF, SHOCK | System-wide | Bot activation, signal gating, all output labels |
| Layer 2: Ticker Volatility | Taxonomy A (5-state) | COMPRESSION, EXPANSION, BLOW_OFF, EXHAUSTION, BREAKDOWN | Per-ticker | Internal scoring only, never shown in outputs without Layer 1 context |

**Invariant**: The mapped 8-state label from `system_state.json:regime` is the ONLY regime label used in user-facing outputs. No module may classify regime independently for output purposes. The internal 5-state taxonomy is for scoring computations only and MUST NOT appear in Telegram messages, PDF headers, or War Room badges.

### 2.3 DataHealth

| Property | Value |
|----------|-------|
| **Authoritative Source** | `system_state.json:data_reliability` + `provenance_chain` per signal |
| **Writer** | Provenance engine (per PROVENANCE_SPEC W3) |
| **Readers** | All output channels, signal qualification pipeline, sanity gates |
| **Refresh Cadence** | Every scan tick (60s) |

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `overall_coverage_pct` | `float [0.0-100.0]` | Percentage of tickers with fresh, valid data |
| `per_ticker_coverage` | `object` | Map of ticker -> coverage percentage |
| `stale_tickers` | `array[string]` | Tickers whose data has exceeded TTL |
| `provider_health` | `object` | Map of provider_id -> { status, last_success_utc, error_count } |
| `last_check_utc` | `ISO8601` | When data health was last evaluated |

**Invariant**: If `data_reliability < 0.80` (80%), the system enters DEGRADED mode. No component may use unchecked data. Signals generated from tickers in the `stale_tickers` list are automatically blocked at the qualification gate.

### 2.4 ScanHealth

| Property | Value |
|----------|-------|
| **Authoritative Source** | `artifacts/scan_health.json` |
| **Writer** | Main scan loop (`main.py`) |
| **Readers** | War Room (System Health panel), Telegram (heartbeat messages), Continuous Integrity Monitor |
| **Refresh Cadence** | Every scan tick (60s) |

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `tick_count` | `int` | Total scan ticks since session start |
| `engine_runs` | `int` | Total engine evaluation runs (may differ from tick_count if ticks are skipped) |
| `p95_cycle_ms` | `float` | 95th percentile scan cycle duration in milliseconds |
| `last_tick_utc` | `ISO8601` | Timestamp of last completed scan tick |
| `is_alive` | `bool` | Whether the engine considers itself healthy |
| `scan_budget_pct` | `float [0.0-100.0]` | Percentage of 60s budget consumed by last cycle |

**Invariant**: If `is_alive = false` OR `last_tick_utc` is older than 5 minutes, the system is STALE. All output channels MUST suppress new outputs until liveness is restored. The War Room MUST display a STALE badge.

---

## 3. ARTIFACT CONTRACTS (CANONICAL SCHEMAS)

Each artifact file has a defined schema, sole writer, enumerated consumers, freshness requirement, and must-exist rule.

### 3.1 `artifacts/system_state.json` -- Session State

| Property | Value |
|----------|-------|
| **Canonical Path** | `artifacts/system_state.json` |
| **Writer** | `command_center/state.py` |
| **Consumers** | War Room API, Telegram pipeline, PDF generator, Go-Live Gate, Continuous Integrity Monitor |
| **Freshness Requirement** | Max 120s (2 scan ticks). Stale after 120s from `last_heartbeat_utc`. |
| **Must-Exist Rule** | If missing at startup, engine creates with defaults (`mode=PAPER`, `kill_switch=false`, `startup_gate_passed=false`). If missing during runtime, all outputs suppressed until file is recreated. |

**Schema:**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `mode` | `string` | YES | `PAPER` / `LIVE` / `BACKTEST` | `command_center/state.py` | All |
| `regime` | `string` | YES | Canonical 8-state market regime | `core/regime_mapping.py` via state writer | Telegram, PDF, War Room, drought manager |
| `data_reliability` | `float` | YES | Overall data health [0.0-1.0] | Provenance engine via state writer | Signal qualifier, output gates |
| `signals_emitted` | `int` | YES | Session signal count | Main scan loop via state writer | War Room, daily summary |
| `kill_switch` | `bool` | YES | Emergency stop state | Operator command / auto-trigger via state writer | All output channels |
| `drought_state` | `object` | YES | `{ tier: int, entered_utc: ISO8601, reason: string }` | Drought manager via state writer | Telegram, PDF, War Room |
| `engine_runs` | `int` | YES | Scan cycle count | Main scan loop via state writer | War Room, scan health |
| `last_heartbeat_utc` | `ISO8601` | YES | Last scan tick timestamp | Main scan loop via state writer | Liveness detection |
| `run_id` | `string` | YES | Session UUID (v4) | State writer at startup | All outputs (embedded in every message/report) |
| `config_hash` | `string` | YES | SHA-256 of settings.yaml | State writer at startup | Integrity monitor |
| `startup_gate_passed` | `bool` | YES | Startup readiness status | Startup gate evaluator via state writer | Output suppression logic |

### 3.2 `artifacts/plays.json` -- Generated Plays / Signals

| Property | Value |
|----------|-------|
| **Canonical Path** | `artifacts/plays.json` |
| **Writer** | Signal qualification pipeline (`qualification/qualifier.py`) |
| **Consumers** | Telegram pipeline, PDF generator, War Room signal feed, Learning Loop |
| **Freshness Requirement** | Max 120s. Stale plays are not resent but remain for historical reference. |
| **Must-Exist Rule** | If missing, created as empty array `[]`. No signals are emitted until file exists. |

**Schema (per play entry):**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `play_id` | `string (uuid)` | YES | Unique play identifier | Qualifier | All |
| `run_id` | `string (uuid)` | YES | Session ID (must match system_state.run_id) | Qualifier | Integrity monitor |
| `ticker` | `string` | YES | Instrument symbol (e.g., `QQQ3.L`) | Strategy engine | All |
| `strategy` | `string` | YES | Strategy ID (e.g., `S15`) | Strategy engine | Telegram, PDF, War Room |
| `direction` | `string` | YES | `LONG` / `SHORT` | Strategy engine | All |
| `entry_price` | `float` | YES | Suggested entry price | Strategy engine | Telegram, War Room |
| `stop_price` | `float` | YES | Stop-loss price | Strategy engine | Telegram, War Room |
| `target_price` | `float` | YES | Profit target price | Strategy engine | Telegram, War Room |
| `confidence` | `int [0-100]` | YES | Confidence score (clamped) | Confidence scorer | All |
| `score` | `float` | YES | Composite qualification score | Qualifier | All |
| `regime_at_signal` | `string` | YES | Regime at time of signal (from system_state) | Qualifier | Telegram, PDF |
| `provenance` | `object` | YES | Provenance record per PROVENANCE_SPEC | Provenance engine | Audit, PDF |
| `as_of_utc` | `ISO8601` | YES | Timestamp of signal generation | Qualifier | All |
| `gates_passed` | `array[string]` | YES | List of gate IDs that passed | Gate runner | Audit |
| `gates_failed` | `array[string]` | NO | List of gate IDs that failed (empty if all passed) | Gate runner | Audit |
| `status` | `string` | YES | `QUALIFYING` / `SENT` / `BLOCKED` / `EXPIRED` | Qualifier / Telegram | Learning Loop |

### 3.3 `artifacts/scan_health.json` -- Scan Timing and SLA

| Property | Value |
|----------|-------|
| **Canonical Path** | `artifacts/scan_health.json` |
| **Writer** | Main scan loop (`main.py`) |
| **Consumers** | War Room (System Health panel), Continuous Integrity Monitor |
| **Freshness Requirement** | Max 120s. If older than 5 minutes, system declared STALE. |
| **Must-Exist Rule** | If missing, created with `is_alive=false`. War Room displays STALE badge. |

**Schema:**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `tick_count` | `int` | YES | Total scan ticks this session | `main.py` | War Room |
| `engine_runs` | `int` | YES | Total engine evaluation runs | `main.py` | War Room |
| `p95_cycle_ms` | `float` | YES | P95 cycle duration (milliseconds) | `main.py` | War Room, SLA monitor |
| `last_tick_utc` | `ISO8601` | YES | Last tick timestamp | `main.py` | Liveness check |
| `is_alive` | `bool` | YES | Engine self-assessment of health | `main.py` | All |
| `scan_budget_pct` | `float` | YES | Budget consumed by last cycle [0-100] | `main.py` | War Room |
| `cycle_history` | `array[object]` | NO | Last 60 cycle timings for trend analysis | `main.py` | War Room trend chart |
| `errors_last_hour` | `int` | YES | Count of scan errors in the last 60 minutes | `main.py` | War Room, alerting |

### 3.4 `artifacts/drought.json` -- Drought State Machine

| Property | Value |
|----------|-------|
| **Canonical Path** | `artifacts/drought.json` |
| **Writer** | Drought manager (`drought_manager.py`) |
| **Consumers** | Telegram pipeline, PDF generator, War Room, system_state writer |
| **Freshness Requirement** | Max 120s. Drought state transitions must be written immediately. |
| **Must-Exist Rule** | If missing, created with `tier=0, state=NORMAL`. Drought detection proceeds normally from clean state. |

**Schema:**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `tier` | `int [0-4]` | YES | Current drought tier (0=NORMAL, 1=WATCH, 2=WARNING, 3=DROUGHT, 4=SEVERE) | Drought manager | All |
| `state` | `string` | YES | Human-readable state label | Drought manager | Telegram, War Room |
| `entered_utc` | `ISO8601` | YES | When the current tier was entered | Drought manager | All |
| `duration_hours` | `float` | YES | Hours in current tier | Drought manager | Telegram, PDF |
| `signals_since_last` | `int` | YES | Signals generated since last successful trade | Drought manager | Telegram |
| `last_signal_utc` | `ISO8601` | NO | Timestamp of last qualifying signal (null if none) | Drought manager | War Room |
| `regime_at_entry` | `string` | YES | Regime when drought tier was entered | Drought manager | Contradiction detector |
| `contradiction_flag` | `bool` | YES | True if EXPANSION regime + DROUGHT tier (contradiction) | Drought manager | Telegram (alert), War Room |

### 3.5 `data/telegram_debug.jsonl` -- Telegram Delivery Audit Log

| Property | Value |
|----------|-------|
| **Canonical Path** | `data/telegram_debug.jsonl` |
| **Writer** | Telegram pipeline (`telegram_bot.py`) |
| **Consumers** | Audit tooling, post-mortem analysis, Continuous Integrity Monitor |
| **Freshness Requirement** | Append-only; no freshness requirement. Log rotation at 100MB. |
| **Must-Exist Rule** | If missing, created on first Telegram send. No impact on operation. |

**Schema (per JSONL line):**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `timestamp_utc` | `ISO8601` | YES | When the send was attempted | Telegram pipeline | Audit |
| `message_type` | `string` | YES | Message type ID per TELEGRAM_TAPE_SPEC | Telegram pipeline | Audit |
| `run_id` | `string` | YES | Session ID | Telegram pipeline | Integrity monitor |
| `play_id` | `string` | NO | Associated play ID (null for non-signal messages) | Telegram pipeline | Audit |
| `regime_label` | `string` | YES | Regime label used in the message | Telegram pipeline | Integrity monitor |
| `gates_checked` | `array[string]` | YES | Output gates evaluated before send | Telegram pipeline | Audit |
| `gates_result` | `string` | YES | `ALL_PASS` / `BLOCKED:{gate_id}` | Telegram pipeline | Audit |
| `send_result` | `string` | YES | `OK` / `RATE_LIMITED` / `API_ERROR:{code}` / `DEDUPE_SKIP` | Telegram pipeline | Audit |
| `message_length` | `int` | YES | Character count of the message body | Telegram pipeline | Audit |
| `dedupe_key` | `string` | YES | Deduplication fingerprint | Telegram pipeline | Audit |

### 3.6 `data/pdf_qa_log.jsonl` -- PDF QA Audit Log

| Property | Value |
|----------|-------|
| **Canonical Path** | `data/pdf_qa_log.jsonl` |
| **Writer** | PDF generator (`pdf_v2_momentum.py`, `pdf_v2_risk.py`) |
| **Consumers** | Audit tooling, post-mortem analysis |
| **Freshness Requirement** | Append-only; no freshness requirement. Log rotation at 50MB. |
| **Must-Exist Rule** | If missing, created on first PDF generation. No impact on operation. |

**Schema (per JSONL line):**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `timestamp_utc` | `ISO8601` | YES | When the PDF was generated | PDF generator | Audit |
| `pdf_type` | `string` | YES | `MOMENTUM` / `RISK` | PDF generator | Audit |
| `run_id` | `string` | YES | Session ID | PDF generator | Integrity monitor |
| `regime_label` | `string` | YES | Regime label rendered in PDF header | PDF generator | Integrity monitor |
| `tickers_included` | `int` | YES | Number of tickers rendered | PDF generator | Audit |
| `tickers_stale` | `int` | YES | Number of tickers with stale data (flagged) | PDF generator | Audit |
| `data_reliability` | `float` | YES | Data reliability at time of generation | PDF generator | Audit |
| `page_count` | `int` | YES | Number of pages in the generated PDF | PDF generator | Audit |
| `generation_ms` | `float` | YES | Time to generate the PDF in milliseconds | PDF generator | Performance tracking |
| `output_path` | `string` | YES | Filesystem path of the generated PDF | PDF generator | Audit |
| `gates_result` | `string` | YES | `ALL_PASS` / `BLOCKED:{gate_id}` | PDF generator | Audit |

### 3.7 `data/go_live_gate_log.jsonl` -- Go-Live Gate Evaluation Log

| Property | Value |
|----------|-------|
| **Canonical Path** | `data/go_live_gate_log.jsonl` |
| **Writer** | Go-Live Gate evaluator (`command_center/go_live_gate.py`) |
| **Consumers** | Operator review, OPS_GOVERNANCE_PLAN compliance |
| **Freshness Requirement** | Append-only; no freshness requirement. |
| **Must-Exist Rule** | If missing, created on first Go-Live evaluation. System remains in PAPER mode until gate passes. |

**Schema (per JSONL line):**

| Field | Type | Required | Description | Writer | Consumers |
|-------|------|----------|-------------|--------|-----------|
| `timestamp_utc` | `ISO8601` | YES | When the evaluation was performed | Go-Live Gate | Operator |
| `run_id` | `string` | YES | Session ID | Go-Live Gate | Audit |
| `criteria` | `array[object]` | YES | List of `{ criterion_id, name, threshold, actual, passed }` | Go-Live Gate | Operator |
| `overall_result` | `string` | YES | `PASS` / `FAIL` | Go-Live Gate | Operator, state writer |
| `blocking_criteria` | `array[string]` | NO | IDs of criteria that failed (empty if PASS) | Go-Live Gate | Operator |
| `days_paper_traded` | `int` | YES | Total paper trading days completed | Go-Live Gate | Operator |
| `win_rate_pct` | `float` | YES | Paper trading win rate | Go-Live Gate | Operator |
| `max_drawdown_pct` | `float` | YES | Maximum drawdown during paper trading | Go-Live Gate | Operator |
| `operator_override` | `bool` | YES | Whether an operator override was used | Go-Live Gate | Audit |

### 3.8 `config/settings.yaml` -- Configuration (Immutable During Session)

| Property | Value |
|----------|-------|
| **Canonical Path** | `config/settings.yaml` |
| **Writer** | Operator (manual edits only, between sessions) |
| **Consumers** | All modules at startup; config hash verified hourly by integrity monitor |
| **Freshness Requirement** | Not applicable (immutable during session) |
| **Must-Exist Rule** | If missing, engine MUST NOT start. Fatal error with clear message. |

**Contract Rules:**

| Rule | Description |
|------|-------------|
| **Immutability** | `config/settings.yaml` MUST NOT be modified while the engine is running. All configuration changes require engine restart. |
| **Hash Verification** | SHA-256 hash computed at startup, stored in `system_state.json:config_hash`. Integrity monitor re-hashes hourly. Mismatch -> DEGRADED mode + Telegram alert. |
| **Schema Validation** | Engine validates all required fields at startup. Missing required field -> fatal startup error. |
| **Version Tracking** | Config changes tracked in `data/config_changelog.jsonl` with `{ timestamp, field, old_value, new_value, operator }`. |

---

## 4. CROSS-COMPONENT INVARIANTS

These invariants MUST hold at all times across the running system. Violation of any invariant triggers the specified enforcement action.

| # | Invariant | Enforcement | Detection | Severity |
|---|-----------|-------------|-----------|----------|
| INV-1 | War Room, Telegram, and PDFs reference the same `run_id` for a given session | `run_id` embedded in run manifest at startup and propagated to all outputs via `system_state.json` | Continuous Integrity Monitor compares `run_id` across `system_state.json`, latest Telegram debug log entry, and latest PDF QA log entry every 60s | CRITICAL |
| INV-2 | `config_hash` matches across all components throughout the session | Config hash computed at startup from `settings.yaml`, stored in `system_state.json:config_hash`, verified hourly by integrity monitor | Hash mismatch -> system enters DEGRADED mode, Telegram alert sent, outputs suppressed until operator acknowledges | CRITICAL |
| INV-3 | `as_of` timestamps across related outputs (Telegram message, PDF section, War Room panel for the same signal) within 60s of each other | Provenance chain carries `as_of` from data source through to output; all outputs for a given play reference the play's `as_of_utc` | Wiring test compares timestamps across output audit logs; divergence > 60s triggers alert | HIGH |
| INV-4 | No component may compute regime independently for output purposes | All output modules read regime from `system_state.json:regime`; internal 5-state taxonomy confined to scoring modules | Code review gate + automated grep for `RegimeClassifier` or `classify_regime` calls outside of `volatility_regime.py` and `regime_mapping.py` | CRITICAL |
| INV-5 | Telegram and PDF sends are blocked unless health gates PASS | `gated_send()` wrapper checks all applicable gates (staleness, magnitude, consistency, score floor, rate governance) before every send per OUTPUT_POLICY_SPEC | Gate bypass attempt -> CRITICAL alert; all sends logged in audit JSONL with `gates_checked` and `gates_result` | CRITICAL |
| INV-6 | Engine startup MUST NOT emit trade outputs until startup readiness gate PASS | Quiet mode engaged on boot; `startup_gate_passed` in `system_state.json` set to `false`; output channels check this flag before emitting | Startup gate log entry recorded; any signal sent before `startup_gate_passed=true` -> CRITICAL bug | CRITICAL |
| INV-7 | Kill switch state persists across restarts | Kill switch state stored in SQLite DB (`data/nzt48.db`) and restored on startup; also written to `system_state.json` | Kill switch persistence test: engage kill switch, restart engine, verify kill switch still engaged | CRITICAL |
| INV-8 | Dedupe window persists across restarts | Active dedupe fingerprints stored in SQLite DB with expiry timestamps; restored on startup; prevents post-restart duplicate sends | Dedupe persistence test: send signal, restart engine, attempt resend of same signal within dedupe window, verify suppression | HIGH |
| INV-9 | Drought state consistent with regime (EXPANSION + DROUGHT = contradiction alert) | Contradiction detector in drought manager compares `system_state.json:regime` with `drought.json:tier` on every state update | Contradiction detected -> Telegram alert with `[CONTRADICTION]` tag; War Room displays contradiction badge; logged for forensic review | HIGH |
| INV-10 | All artifact writes are atomic (no partial writes visible to readers) | Write to temporary file (`.tmp` suffix in same directory), then atomic `os.rename()` to final path | Integrity monitor validates JSON parse-ability of all artifact files every 60s; parse failure -> CRITICAL alert | CRITICAL |
| INV-11 | Signal play_id is globally unique across all sessions | UUID v4 generation; collision probability negligible but checked against DB on insert | Duplicate play_id on insert -> reject signal, log error | HIGH |
| INV-12 | Every signal reaching Telegram has a corresponding entry in `plays.json` | Telegram pipeline reads play_id from signal payload and verifies existence in `plays.json` before formatting | play_id not found -> signal suppressed, audit log entry with `ORPHAN_SIGNAL` tag | HIGH |
| INV-13 | All monetary values use consistent currency denomination within a session | ISA mode -> GBP; Global mode -> USD; no mixing within a single output | Integrity monitor checks currency field consistency across plays.json entries | MEDIUM |
| INV-14 | Provenance chain unbroken from data source to output | Every data field in outputs carries provenance metadata per PROVENANCE_SPEC; no `null` provenance in qualifying signals | Provenance audit checks for null/missing provenance in plays.json; null provenance -> signal blocked at qualification | HIGH |

---

## 5. INTERFACE CONTRACTS BETWEEN MAJOR MODULES

### 5.1 Engine -> Artifact Store

| Property | Value |
|----------|-------|
| **Method** | File I/O (JSON write to `artifacts/` directory) |
| **Protocol** | Atomic write (write to `.tmp`, then `os.rename()`) |
| **Frequency** | Every scan tick (60s) for state files; on-demand for signals |
| **Latency SLA** | All artifact writes complete within 500ms of scan tick |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.1.1 | Engine writes `system_state.json` on EVERY scan tick, even if no signals generated | Missing heartbeat -> STALE detection triggers within 5 minutes |
| 5.1.2 | Engine writes `scan_health.json` on EVERY scan tick with updated timing metrics | War Room loses health visibility; SLA monitoring blind |
| 5.1.3 | Engine appends to `plays.json` only through the qualification pipeline | Unqualified signals reaching the artifact -> all gates bypassed (CRITICAL) |
| 5.1.4 | Engine MUST NOT delete or truncate artifact files during a session | Loss of audit trail; consumers receive empty/corrupt state |
| 5.1.5 | All writes include `run_id` matching the session's `system_state.json:run_id` | INV-1 violation; cross-output consistency broken |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| Disk full | Engine logs CRITICAL, sends Telegram alert (if possible), enters DEGRADED mode, suppresses new artifact writes |
| Permission denied | Engine logs CRITICAL, attempts alternative path (`/tmp/nzt48_fallback/`), sends alert |
| JSON serialisation error | Engine logs the raw data, writes empty-but-valid JSON (`{}`), marks data_reliability as 0.0 |

### 5.2 Artifact Store -> War Room API

| Property | Value |
|----------|-------|
| **Method** | File read (FastAPI reads JSON from `artifacts/` directory) + SQLite DB queries |
| **Protocol** | REST API (`/api/*` endpoints) + WebSocket (`/ws/live`) for push updates |
| **Frequency** | REST: per-panel poll intervals (5s-300s per WAR_ROOM_REQUIREMENTS_SPEC); WebSocket: real-time push on state change |
| **Latency SLA** | API response within 200ms for all artifact-backed endpoints |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.2.1 | API reads `system_state.json` for regime, mode, and kill switch status | War Room shows stale or incorrect system state |
| 5.2.2 | API reads `plays.json` for signal feed panel | Signal feed shows stale or missing signals |
| 5.2.3 | API reads `scan_health.json` for system health panel | Health panel shows stale data; operator loses visibility |
| 5.2.4 | API validates JSON integrity before serving; returns HTTP 503 if artifact is corrupt | Frontend receives corrupt JSON -> rendering crash |
| 5.2.5 | API includes `X-Run-Id` header in all responses for traceability | Cross-output consistency verification impossible |
| 5.2.6 | API MUST NOT cache artifact data beyond the artifact's freshness requirement | War Room shows stale data that has already been superseded |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| Artifact file missing | API returns HTTP 503 with `{ "error": "artifact_missing", "artifact": "<name>" }` |
| Artifact file corrupt (invalid JSON) | API returns HTTP 503 with `{ "error": "artifact_corrupt", "artifact": "<name>" }` |
| Artifact file stale (exceeds freshness) | API returns data with `X-Stale: true` header; frontend displays staleness warning |

### 5.3 Artifact Store -> Telegram Pipeline

| Property | Value |
|----------|-------|
| **Method** | File read (Telegram pipeline reads from `artifacts/`) + in-memory state |
| **Protocol** | Internal function calls within the engine process |
| **Frequency** | On-demand (signal events) + scheduled (status messages per TELEGRAM_TAPE_SPEC) |
| **Latency SLA** | Signal -> Telegram delivery within 5s of qualification |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.3.1 | Telegram reads regime label ONLY from `system_state.json:regime` | INV-4 violation; regime label mismatch across outputs |
| 5.3.2 | Telegram reads play data from `plays.json` using `play_id` lookup | Orphan signal (INV-12 violation) |
| 5.3.3 | Telegram pipeline calls `gated_send()` for EVERY message, no exceptions | INV-5 violation; unqualified output reaches operator |
| 5.3.4 | Every send attempt logged to `data/telegram_debug.jsonl` | Audit trail broken; post-mortem capability degraded |
| 5.3.5 | Telegram pipeline checks `startup_gate_passed` before any signal sends | INV-6 violation; premature signal delivery |
| 5.3.6 | Telegram pipeline checks `kill_switch` before any output | Kill switch bypass (CRITICAL) |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| Telegram API error (rate limited) | Exponential backoff (1s, 2s, 4s, max 60s); log to debug JSONL; do not retry beyond 3 attempts for non-signal messages |
| Telegram API error (network) | Retry 3 times with 5s intervals; if all fail, log CRITICAL, buffer message for next tick |
| Artifact file missing | Suppress message, log warning, continue to next tick |
| Artifact file stale | Include `[STALE DATA]` tag in message if sending status updates; block signal sends entirely |

### 5.4 Artifact Store -> PDF Generator

| Property | Value |
|----------|-------|
| **Method** | File read (PDF generator reads from `artifacts/` and `data/`) + direct data queries |
| **Protocol** | Internal function calls within the engine process |
| **Frequency** | Scheduled (daily PDF generation per delivery schedule) |
| **Latency SLA** | PDF generation completes within 60s |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.4.1 | PDF reads regime label ONLY from `system_state.json:regime` for header badge | INV-4 violation; regime mismatch between PDF and Telegram |
| 5.4.2 | PDF includes `run_id` in footer metadata | INV-1 violation; traceability broken |
| 5.4.3 | PDF flags stale tickers using provenance TTL from PROVENANCE_SPEC | Stale data presented without warning (trust violation) |
| 5.4.4 | PDF generation logged to `data/pdf_qa_log.jsonl` | Audit trail broken |
| 5.4.5 | PDF applies leverage-once policy for all return calculations | Double-leverage bug (CRITICAL per SANITY_GATE_SPEC) |
| 5.4.6 | PDF passes all output gates before file delivery | INV-5 violation |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| Artifact file missing | PDF generated with `[DATA UNAVAILABLE]` sections clearly marked; logged as DEGRADED |
| Data provider outage | PDF generated with available data; stale sections flagged; `data_reliability` shown in header |
| Generation timeout (>60s) | Abort generation, log CRITICAL, send Telegram alert, retry on next scheduled run |
| LaTeX/rendering error | Fallback to simplified template; log error with full stack trace |

### 5.5 Engine -> Learning Loop

| Property | Value |
|----------|-------|
| **Method** | SQLite DB (`data/nzt48.db`) + artifact reads |
| **Protocol** | DB writes for signal outcomes; artifact reads for signal metadata |
| **Frequency** | On trade close (win/loss determined) or at session end for open positions |
| **Latency SLA** | Outcome recorded within 60s of trade close |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.5.1 | Every qualifying signal from `plays.json` has a corresponding entry in the learning DB | Learning Loop has incomplete data; edge calculations skewed |
| 5.5.2 | Outcome recorded with original `play_id`, `strategy`, `regime_at_signal`, and `confidence` | Attribution analysis impossible; cannot determine which regime/strategy combos have edge |
| 5.5.3 | Outcome includes `entry_price`, `exit_price`, `pnl_pct`, `r_multiple`, `duration_minutes` | Insufficient data for edge recalculation |
| 5.5.4 | Learning Loop MUST NOT modify `plays.json` or `system_state.json` | Read-only consumer; writing violates single-writer rule |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| DB write failure | Retry 3 times; if all fail, buffer in memory and retry next tick; log CRITICAL |
| Orphan signal (play_id not in plays.json) | Log warning; record what data is available; flag for manual review |
| Stale outcome (trade closed >24h ago) | Accept but flag with `stale_outcome=true`; exclude from real-time edge updates |

### 5.6 Learning Loop -> Edge Ledger

| Property | Value |
|----------|-------|
| **Method** | SQLite DB (`data/nzt48.db`) |
| **Protocol** | DB reads from learning tables; DB writes to edge ledger tables |
| **Frequency** | Recalculated after every outcome recording; full recalc daily |
| **Latency SLA** | Edge update within 120s of outcome recording |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.6.1 | Edge Ledger computed from COMPLETE outcome history, not just recent trades | Survivorship bias; edge estimates unreliable |
| 5.6.2 | Edge Ledger partitioned by `strategy + regime + ticker_class` | Aggregate edge masks per-segment performance; cannot optimise allocation |
| 5.6.3 | Edge Ledger includes `sample_size`, `confidence_interval`, and `last_updated_utc` | Consumers cannot assess statistical significance |
| 5.6.4 | Edge Ledger MUST NOT be used if `sample_size < 30` for a given segment | Insufficient data -> fall back to prior assumptions defined in `settings.yaml` |
| 5.6.5 | Edge Ledger is read-only for all modules except the Learning Loop | Multiple writers -> data corruption |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| Insufficient data for a segment | Edge value set to `null`; consumers fall back to config defaults |
| DB corruption | Rebuild edge ledger from raw outcome history; log CRITICAL; Telegram alert |
| Calculation error (NaN/Inf) | Reject update; retain previous edge value; log error with full context |

### 5.7 Provenance Engine -> All Consumers

| Property | Value |
|----------|-------|
| **Method** | In-memory provenance records attached to data objects; persisted in `system_state.json:data_reliability` |
| **Protocol** | Every data fetch returns `(value, provenance_record)` tuple |
| **Frequency** | Every data access |
| **Latency SLA** | Provenance overhead < 5ms per data point |

**Contract:**

| # | Rule | Consequence of Violation |
|---|------|--------------------------|
| 5.7.1 | Every data field consumed by any output module MUST have a non-null provenance record | INV-14 violation; untracked data in outputs |
| 5.7.2 | Provenance records follow the schema defined in PROVENANCE_SPEC Section 2 | Schema mismatch -> provenance parsing fails in consumers |
| 5.7.3 | Staleness detection uses `as_of + ttl_seconds` from the provenance record | Stale data served without warning |
| 5.7.4 | Quality score < 0.3 triggers automatic rejection of the data field | Low-quality data used in scoring or output |
| 5.7.5 | Provenance records are immutable once attached; no modification after creation | Audit trail integrity compromised |

**Error Handling:**

| Failure | Behaviour |
|---------|-----------|
| Provider returns no data | Provenance record created with `quality=0.0`, `provider=<primary>`; fallback provider attempted |
| Provider returns partial data | Provenance record created with degraded `quality` score (0.3-0.7 depending on completeness) |
| All providers fail for a ticker | Ticker added to `stale_tickers[]`; signals for that ticker blocked; `data_reliability` decremented |

---

## 6. FAILURE MODE TABLE

Integration-level failure modes (not individual component failures). Each failure involves the boundary between two or more components.

| # | Failure | Detection | Impact | Mitigation |
|---|---------|-----------|--------|------------|
| F-INT-01 | **Artifact write race condition**: Two writers attempt to write the same artifact file concurrently | Integrity monitor detects corrupt JSON (partial write) | Consumers read corrupt state; potential cascade of bad outputs | Enforce single-writer rule per artifact (Section 3); atomic write via temp-then-rename; file locking as defence in depth |
| F-INT-02 | **run_id divergence**: Telegram sends a message with a different `run_id` than the current session | Integrity monitor compares `run_id` in Telegram debug log vs `system_state.json` every 60s | Traceability broken; cannot correlate outputs to the correct session | Telegram pipeline reads `run_id` from `system_state.json` on every send, never caches it beyond the current tick |
| F-INT-03 | **Config hash mismatch**: `settings.yaml` modified while engine is running | Hourly hash verification by integrity monitor | System operating with different config than recorded at startup; all reproducibility guarantees void | DEGRADED mode; suppress outputs; Telegram alert; require engine restart |
| F-INT-04 | **Regime label split-brain**: Telegram shows `TRENDING_UP_STRONG` while PDF shows `RANGE_BOUND` for the same time window | Integrity monitor compares regime labels in Telegram debug log and PDF QA log within the same 60s window | Operator receives contradictory intelligence; trust in system destroyed | INV-4 enforcement: all outputs read regime from single source (`system_state.json:regime`) |
| F-INT-05 | **Stale artifact served as fresh**: War Room API serves `system_state.json` that is 10 minutes old without staleness indication | API checks `last_heartbeat_utc` against current time; returns `X-Stale: true` header if beyond freshness threshold | Operator makes decisions based on outdated state | API-level freshness check on every read; frontend displays staleness badge when `X-Stale: true` |
| F-INT-06 | **Kill switch lost on restart**: Kill switch engaged, engine restarts, kill switch not restored | Kill switch persistence test in startup sequence; engine reads kill switch state from DB before first tick | Engine resumes emitting signals when it should be stopped; potential capital loss in LIVE mode | Store kill switch in SQLite DB; restore on startup BEFORE opening output channels; startup gate checks kill switch state |
| F-INT-07 | **Dedupe window lost on restart**: Engine restarts, duplicate signal sent because dedupe fingerprints were in-memory only | Post-restart duplicate detection by Telegram pipeline; operator receives duplicate alert | Operator acts on duplicate signal; potential double-entry into a position | Store active dedupe fingerprints in SQLite DB with expiry; restore on startup; Telegram pipeline verifies against DB before every send |
| F-INT-08 | **Provenance chain broken**: Signal reaches Telegram with null provenance for one or more data fields | Qualification gate checks for complete provenance chain; any null provenance -> signal blocked | Signal published without data quality assurance; stale or incorrect data may be presented as fresh | INV-14 enforcement: provenance gate in qualification pipeline; block signals with incomplete provenance |
| F-INT-09 | **Learning Loop orphan**: Trade outcome recorded but no matching `play_id` in `plays.json` | Learning Loop validates `play_id` existence before recording outcome | Edge calculations include untracked signals; edge ledger becomes unreliable | Learning Loop performs `play_id` lookup; orphan outcomes logged with `ORPHAN` tag; excluded from edge calculations until manually resolved |
| F-INT-10 | **Timestamp skew across outputs**: Telegram message `as_of` differs from PDF `as_of` for the same data by > 60s | Wiring test compares timestamps in audit logs | Operator sees different timestamps for the same data point across channels | All outputs use the `as_of` from the provenance record attached to the data, not the time of output generation |
| F-INT-11 | **Drought-regime contradiction undetected**: Regime is EXPANSION (bullish, high opportunity) while drought tier is 3 (DROUGHT, no signals qualifying) | Contradiction detector in drought manager fires on every regime/drought update | System in contradictory state without operator awareness; may indicate broken scoring pipeline | Explicit contradiction check: if `regime in [TRENDING_UP_STRONG, TRENDING_UP_MOD]` AND `drought.tier >= 3`, fire `[CONTRADICTION]` Telegram alert |
| F-INT-12 | **Startup signal leak**: Engine sends signals during startup before data providers are fully initialised | Startup readiness gate checks data provider health, initial scan completion, and artifact integrity before setting `startup_gate_passed=true` | Signals based on incomplete or stale data from previous session reach operator | INV-6 enforcement: quiet mode on boot; all output channels check `startup_gate_passed` before emitting |
| F-INT-13 | **Edge Ledger stale**: Learning Loop fails to update edge ledger after outcomes are recorded | Edge ledger `last_updated_utc` checked by consumers; alert if >24h stale | Strategy allocation based on outdated edge estimates | Consumers check `last_updated_utc`; if >24h stale, fall back to config defaults; Telegram alert sent |
| F-INT-14 | **Artifact directory missing**: `artifacts/` directory deleted or unmounted | Engine checks directory existence on startup and every 60s | All artifact writes fail; all consumers receive errors | Engine recreates directory on detection; DEGRADED mode until all artifacts regenerated; Telegram alert |

---

## 7. OPERATOR ACTIONS

Defined responses for integration failure scenarios. The operator is the sole human authority over the system.

| # | Scenario | Action |
|---|----------|--------|
| OA-1 | **Integrity monitor reports `run_id` mismatch across outputs** | 1. Check Telegram debug log and PDF QA log for conflicting `run_id` values. 2. If mismatch is between current and stale session, confirm old outputs are from a previous run. 3. If mismatch is within the current session, STOP engine immediately (`docker-compose stop nzt48`). 4. Inspect `system_state.json` for correct `run_id`. 5. Restart engine. 6. Verify all outputs reference the new `run_id`. |
| OA-2 | **Config hash mismatch detected** | 1. Engine auto-enters DEGRADED mode. 2. Identify what changed in `settings.yaml` (diff against stored hash). 3. If change was intentional, restart engine to pick up new config properly. 4. If change was unintentional, restore `settings.yaml` from backup (`data/config_backups/`), then restart. |
| OA-3 | **Kill switch found disengaged after restart when it should be engaged** | 1. IMMEDIATELY re-engage kill switch via War Room or Telegram command. 2. Audit `data/nzt48.db` for kill switch persistence entries. 3. Check startup logs for kill switch restoration step. 4. File incident report. 5. Do NOT resume trading until root cause identified. |
| OA-4 | **Drought-regime contradiction alert received** | 1. Review current regime classification (`system_state.json:regime`). 2. Review drought state (`artifacts/drought.json`). 3. Check if the regime transition is recent (regime may be lagging). 4. If legitimate contradiction, investigate scoring pipeline: why are no signals qualifying in an EXPANSION regime? 5. Check data health: are providers returning fresh data? 6. If data health is fine and scoring is correct, the market may be in a genuinely unusual state. Log finding and monitor. |
| OA-5 | **Telegram delivery failing consistently (3+ consecutive failures)** | 1. Check Telegram API status (network, rate limits, bot token validity). 2. Review `data/telegram_debug.jsonl` for error patterns. 3. If rate-limited, reduce message frequency in `settings.yaml` and restart. 4. If API error, verify bot token and chat ID in config. 5. If network error, check EC2 security groups and DNS resolution. 6. Signals continue to be recorded in `plays.json` regardless of Telegram status. |
| OA-6 | **War Room API returning 503 for multiple endpoints** | 1. SSH to EC2: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28`. 2. Check engine status: `docker logs nzt48 --tail 100`. 3. Check artifact directory: `docker exec nzt48 ls -la /app/artifacts/`. 4. If artifacts missing, check disk space: `docker exec nzt48 df -h`. 5. If disk full, archive old logs: move `data/*.jsonl` older than 7 days to `data/archive/`. 6. Restart engine: `docker-compose restart nzt48`. 7. Verify API recovery: `curl http://localhost:8000/api/system-health`. |
| OA-7 | **PDF generation timing out repeatedly** | 1. Check `data/pdf_qa_log.jsonl` for `generation_ms` trend. 2. If increasing over time, check for memory leaks in PDF renderer. 3. Check data provider response times: slow providers cause slow PDF generation. 4. Temporarily switch to simplified PDF template (less data, faster generation). 5. Review ticker count: if ISA universe has expanded, consider batching PDFs. 6. Restart engine if memory leak suspected. |
| OA-8 | **Edge Ledger shows NaN for a strategy-regime segment** | 1. Query learning DB for raw outcomes in that segment: `SELECT * FROM outcomes WHERE strategy='S15' AND regime='TRENDING_UP_STRONG'`. 2. Check for division-by-zero in edge calculation (e.g., zero trades in segment). 3. If legitimate data gap (< 30 trades), the NaN is expected; system falls back to config defaults per contract 5.6.4. 4. If > 30 trades exist and NaN persists, there is a calculation bug. Stop edge ledger updates, revert to config defaults, investigate. |
| OA-9 | **Continuous Integrity Monitor itself becomes unresponsive** | 1. Check engine logs for integrity monitor thread status. 2. If thread crashed, restart engine (integrity monitor starts with engine). 3. If thread is running but not producing output, check for deadlock (thread dump). 4. In the interim, manually verify key invariants: compare `run_id` across outputs, check artifact freshness, verify regime consistency. 5. Do NOT rely on automated invariant checking until monitor is confirmed operational. |
| OA-10 | **Post-restart duplicate signals detected by operator** | 1. Check `data/telegram_debug.jsonl` for duplicate `dedupe_key` entries. 2. Verify dedupe table was restored from DB on startup. 3. If dedupe restoration failed, engage kill switch immediately. 4. Review startup logs for dedupe restoration step. 5. Clear any duplicate signals from operator's action queue. 6. Fix dedupe persistence, restart, verify no further duplicates. |

---

## 8. APPENDIX: CONTRACT VERIFICATION CHECKLIST

The following checklist MUST be executed after any code change that touches cross-component boundaries.

| # | Check | Command / Method | Expected Result |
|---|-------|------------------|-----------------|
| CV-1 | All artifact files are valid JSON | `python -c "import json; [json.load(open(f)) for f in glob('artifacts/*.json')]"` | No exceptions |
| CV-2 | `run_id` consistent across all artifacts | Compare `run_id` in `system_state.json`, latest `plays.json` entry, latest `telegram_debug.jsonl` entry | All match |
| CV-3 | `config_hash` in `system_state.json` matches current `settings.yaml` | `sha256sum config/settings.yaml` vs `system_state.json:config_hash` | Match |
| CV-4 | No independent regime classification in output modules | `grep -rn "classify_regime\|RegimeClassifier" --include="*.py" delivery/ telegram_bot.py` | Zero matches |
| CV-5 | Kill switch persists across restart | Engage kill switch, restart engine, read `system_state.json:kill_switch` | `true` |
| CV-6 | Dedupe window persists across restart | Send test signal, restart engine, attempt resend | Second send suppressed |
| CV-7 | Startup gate blocks pre-init signals | Start engine, immediately check Telegram debug log | No signal messages before `startup_gate_passed=true` |
| CV-8 | Atomic writes in effect | Check all artifact write paths for temp-then-rename pattern | All writes use atomic pattern |
| CV-9 | Provenance chain complete for qualifying signals | Generate test signal, inspect `plays.json` entry provenance field | All data fields have non-null provenance |
| CV-10 | Freshness headers present in War Room API | `curl -v http://localhost:8000/api/regime` | Response includes freshness-related headers |

---

## 9. REVISION HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | NZT-48 Engineering | Initial release. Defines all system-of-record objects, artifact schemas, cross-component invariants, interface contracts, failure modes, and operator actions. |
| 1.1 | 2026-02-27 | NZT-48 Engineering | W13 Addendum: Added startup readiness gate contract, continuous integrity monitor contract, artifact single-source contract, Docker parity check contract. Cross-references to new W13 annexes. |

---

## ADDENDUM: W13 ALWAYS-WIRED CONTRACTS

**Added by**: `docs/ADDENDUM_ALWAYS_WIRED_110.md` v1.0

### W13-C1: Startup Readiness Gate Contract

| Property | Value |
|----------|-------|
| **Authoritative Source** | `artifacts/readiness_gate.json` |
| **Writer** | `core/startup_gate.py` |
| **Readers** | Main orchestrator (gate decision), War Room (readiness checklist), Telegram ([SYSTEM] messages) |
| **When** | On boot + before each session window (06:55, 13:25 UK) |

**Invariant**: No trade output may be emitted until `readiness_gate.json` shows `status: "READY"`. If status is `DEGRADED` or `HALTED`, only [SYSTEM] and [CRITICAL ERROR] messages are permitted.

**Spec**: `annexes/STARTUP_READINESS_GATE_SPEC.md`

### W13-C2: Continuous Integrity Monitor Contract

| Property | Value |
|----------|-------|
| **Authoritative Source** | `artifacts/integrity_status.json` |
| **Writer** | `core/integrity_monitor.py` |
| **Readers** | Main orchestrator (mode management), War Room (System Wiring panel), Telegram ([SYSTEM] alerts) |
| **Cadence** | Every 5 min (market hours), 15 min (outside) |

**Invariant**: If any wiring drift signature is detected for 2 consecutive checks, system MUST enter DEGRADED mode and suppress trade outputs.

**Spec**: `annexes/CONTINUOUS_INTEGRITY_MONITOR_SPEC.md`

### W13-C3: Artifact Single Source Contract

| Property | Value |
|----------|-------|
| **Rule** | All output channels render from artifacts/ directory for a given run_id |
| **Enforcement** | Schema validation on read; run_id cross-check |
| **Violation Response** | Output channel blocked; INTEGRITY ALERT sent |

**Invariant**: For any given scan cycle, the run_id in `plays.json`, `system_state.json`, Telegram messages, PDF reports, and War Room API responses MUST be identical.

**Spec**: `annexes/ARTIFACT_SINGLE_SOURCE_POLICY.md`

### W13-C4: Docker Parity Contract

| Property | Value |
|----------|-------|
| **Rule** | Host code checksums MUST match container code checksums for all critical .py files |
| **Enforcement** | Post-deploy parity check + daily scheduled check |
| **Violation Response** | PARITY_FAIL alert; future deploys blocked until resolved |

**Invariant**: No production Python code may exist solely on the host. All code must be baked into the Docker image via `docker compose build`.

**Spec**: `annexes/EC2_DOCKER_DRIFT_GUARDS.md`

---

**END OF DOCUMENT**

*This specification is BINDING. All cross-component wiring in the NZT-48 system MUST conform to the contracts defined herein. Any deviation constitutes a defect and must be resolved before the affected code path is deployed.*
