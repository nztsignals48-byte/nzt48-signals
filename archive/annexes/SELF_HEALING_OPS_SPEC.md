# NZT-48 Self-Healing Operations Specification

**Document ID:** NZT48-ANNEX-SHO-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** BINDING
**Owner:** NZT-48 Trading Engine
**Classification:** Internal / Operational

---

## Table of Contents

1. [Objective](#1-objective)
2. [Allowed Auto-Actions](#2-allowed-auto-actions)
3. [Requires Human Approval](#3-requires-human-approval)
4. [Action Logging](#4-action-logging)
5. [Escalation Policy](#5-escalation-policy)
6. [Self-Healing Controller Interface](#6-self-healing-controller-interface)
7. [Acceptance Tests](#7-acceptance-tests)
8. [Proof Artifacts](#8-proof-artifacts)
9. [Revision History](#9-revision-history)

---

## 1. Objective

Define the exact boundary between operations the NZT-48 system is permitted to perform autonomously (self-heal) and operations that require explicit human approval before execution. Every automated action must be safe, reversible, and logged with full audit context. No automated action may alter trading logic, risk parameters, or the active universe.

**Design Principle:** Self-healing exists to maintain uptime and data freshness. It does NOT exist to make trading decisions, change risk posture, or modify system behaviour. When in doubt, escalate. A 5-minute delay waiting for operator approval costs nothing; an autonomous action that corrupts state costs everything.

**Scope:** This specification governs all automated recovery actions within the NZT-48 engine (`main.py` orchestrator), the War Room API, and supporting infrastructure. It does NOT govern Docker-level orchestration (restart policies, health checks) -- those remain under operator control exclusively.

---

## 2. Allowed Auto-Actions

The following actions may be performed by the system without human approval. Each action is gated by specific trigger conditions and bounded by safety constraints.

### 2.1 Reload Configuration / Artifact Caches

**Action ID Prefix:** `HEAL-CFG`

**Trigger:** Configuration file (`config/settings.yaml`) modification detected via filesystem watcher, OR artifact cache miss on read.

**Scope:** Re-read `config/settings.yaml` into memory. Refresh the in-memory artifact cache by re-loading from `artifacts/` directory.

**Safety Constraints:**

| Constraint | Rule |
|---|---|
| Schema validation | New config must pass JSON Schema / YAML schema validation before replacing in-memory state. If validation fails, retain previous config and log `HEAL_CFG_SCHEMA_FAIL`. |
| Diff logging | Before replacing config, compute and log the diff (changed keys, old values, new values). |
| Parameter bounds | Numeric parameters must remain within pre-defined min/max bounds (defined in schema). Out-of-bounds values trigger rejection. |
| Rollback | Previous config snapshot is retained in memory for 60 seconds. If the system detects anomalous behaviour within 60s of reload (e.g., scan failure, gate failure spike), auto-rollback to previous config. |

**Implementation:**

```python
@dataclass
class ConfigReloadAction:
    action_type: str = "HEAL-CFG-RELOAD"
    trigger_reason: str = ""
    previous_hash: str = ""
    new_hash: str = ""
    diff: dict = field(default_factory=dict)
    schema_valid: bool = False
    bounds_valid: bool = False

    def execute(self) -> HealResult:
        new_config = load_yaml("config/settings.yaml")

        if not validate_schema(new_config):
            return HealResult(success=False, code="HEAL_CFG_SCHEMA_FAIL")

        if not validate_bounds(new_config):
            return HealResult(success=False, code="HEAL_CFG_BOUNDS_FAIL")

        self.diff = compute_diff(current_config, new_config)
        self.previous_hash = hash_config(current_config)
        self.new_hash = hash_config(new_config)

        apply_config(new_config)
        schedule_rollback_window(timeout=60)

        return HealResult(success=True, code="HEAL_CFG_RELOAD_OK")
```

---

### 2.2 Restart War Room API Process

**Action ID Prefix:** `HEAL-WR`

**Trigger:** War Room health check (`GET /api/health`) fails 3 consecutive times, each spaced at least 10 seconds apart (minimum 30 seconds of confirmed failure before action).

**Scope:** Restart ONLY the War Room FastAPI/uvicorn process. This does NOT restart the trading engine (`main.py`), Docker containers, or any other process.

**Safety Constraints:**

| Constraint | Rule |
|---|---|
| Consecutive failure gate | Exactly 3 failures required. Not 2, not 1. Each check must be at least 10s apart to avoid transient false positives. |
| Cooldown | After a restart, no further auto-restart for 15 minutes. If the War Room fails again within the cooldown, escalate to operator. |
| Max daily restarts | Maximum 3 auto-restarts per 24-hour rolling window. Fourth failure within 24h triggers escalation. |
| Process isolation | Only the War Room API process is restarted. The restart command must be `kill -TERM <pid> && sleep 2 && start_warroom_api()`. Never `kill -9`. |
| State preservation | War Room must reload state from `artifacts/` on restart. No in-memory-only state is acceptable. |

**Implementation:**

```python
@dataclass
class WarRoomRestartAction:
    action_type: str = "HEAL-WR-RESTART"
    consecutive_failures: int = 0
    failure_timestamps: list = field(default_factory=list)
    last_restart_at: datetime = None
    daily_restart_count: int = 0

    COOLDOWN_SECONDS: int = 900          # 15 minutes
    MAX_DAILY_RESTARTS: int = 3
    REQUIRED_FAILURES: int = 3
    MIN_FAILURE_SPACING: int = 10        # seconds

    def should_execute(self) -> tuple[bool, str]:
        if self.consecutive_failures < self.REQUIRED_FAILURES:
            return False, "INSUFFICIENT_FAILURES"

        if not self._failures_properly_spaced():
            return False, "FAILURES_TOO_CLOSE"

        if self.last_restart_at:
            elapsed = (now() - self.last_restart_at).total_seconds()
            if elapsed < self.COOLDOWN_SECONDS:
                return False, "COOLDOWN_ACTIVE"

        if self.daily_restart_count >= self.MAX_DAILY_RESTARTS:
            return False, "DAILY_LIMIT_REACHED"

        return True, "APPROVED"

    def execute(self) -> HealResult:
        ok, reason = self.should_execute()
        if not ok:
            if reason in ("COOLDOWN_ACTIVE", "DAILY_LIMIT_REACHED"):
                escalate_to_operator(reason)
            return HealResult(success=False, code=f"HEAL_WR_{reason}")

        pid = get_warroom_pid()
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        start_warroom_api()

        self.last_restart_at = now()
        self.daily_restart_count += 1
        self.consecutive_failures = 0

        return HealResult(success=True, code="HEAL_WR_RESTART_OK")
```

---

### 2.3 Rotate Logs

**Action ID Prefix:** `HEAL-LOG`

**Trigger:** Any log file exceeds 10 MB, as detected by the existing `RotatingFileHandler` configuration.

**Scope:** Rotate the oversized log file per the existing `RotatingFileHandler` policy (rename to `.1`, `.2`, etc., up to `maxBackup` count). This is the standard Python logging rotation mechanism -- no custom logic.

**Safety Constraints:**

| Constraint | Rule |
|---|---|
| No deletion | Rotation only renames files. Old logs are never deleted by the self-healing system. Log cleanup is an operator-only action. |
| Backup count | Maximum 5 rotated files per log. Oldest rotated file is overwritten only when the 6th rotation occurs. |
| Atomic rename | Use `os.rename()` (atomic on same filesystem). No copy-then-delete. |

**Implementation:**

```python
# Standard Python logging configuration -- no custom code needed.
# Included here for specification completeness.

handler = RotatingFileHandler(
    filename="logs/nzt48_engine.log",
    maxBytes=10 * 1024 * 1024,   # 10 MB
    backupCount=5,
    encoding="utf-8"
)
```

---

### 2.4 Re-Run Readiness Checks

**Action ID Prefix:** `HEAL-RDY`

**Trigger:** Scheduled every 5 minutes via APScheduler. Also triggered on-demand after any config reload or War Room restart.

**Scope:** Re-execute the startup readiness gate sequence: verify data feed connectivity, verify artifact directory is writable, verify Telegram bot token is valid, verify all required config keys are present.

**Safety Constraints:**

| Constraint | Rule |
|---|---|
| Read-only | Readiness checks are read-only probes. They MUST NOT modify any state, write any files, or send any messages. |
| Timeout | Each individual check has a 10-second timeout. Total readiness sequence timeout is 30 seconds. |
| Failure response | If readiness fails, log the failure and update `artifacts/readiness_status.json`. Do NOT stop the engine or suppress signals -- that is the domain of the Output Policy and Sanity Gates. |
| No auto-fix | If a readiness check fails, the system logs the failure and reports it. It does NOT attempt to fix the underlying issue (e.g., it does not try to recreate missing directories or re-authenticate tokens). |

**Implementation:**

```python
@dataclass
class ReadinessCheckAction:
    action_type: str = "HEAL-RDY-CHECK"
    checks: list[str] = field(default_factory=lambda: [
        "data_feed_connectivity",
        "artifact_directory_writable",
        "telegram_bot_token_valid",
        "config_keys_present",
        "war_room_api_responsive",
        "scheduler_running"
    ])

    def execute(self) -> HealResult:
        results = {}
        for check in self.checks:
            try:
                results[check] = run_check(check, timeout=10)
            except TimeoutError:
                results[check] = CheckResult(passed=False, reason="TIMEOUT")

        write_json("artifacts/readiness_status.json", {
            "timestamp": now_iso(),
            "checks": results,
            "all_passed": all(r.passed for r in results.values())
        })

        if not all(r.passed for r in results.values()):
            failed = [k for k, v in results.items() if not v.passed]
            return HealResult(
                success=False,
                code=f"HEAL_RDY_FAIL_{','.join(failed)}"
            )

        return HealResult(success=True, code="HEAL_RDY_ALL_PASSED")
```

---

### 2.5 Clear Stale Artifact Cache Entries

**Action ID Prefix:** `HEAL-ART`

**Trigger:** During each readiness check cycle (every 5 minutes), scan the provenance cache for entries whose `as_of + ttl_seconds` has expired.

**Scope:** Remove TTL-expired provenance records from the in-memory cache. This forces the next data access to fetch fresh values from providers. No on-disk artifacts are deleted.

**Safety Constraints:**

| Constraint | Rule |
|---|---|
| In-memory only | Only the in-memory provenance cache is affected. On-disk artifacts (`artifacts/*.json`) are NEVER deleted by this action. |
| Eviction logging | Every evicted cache key is logged with its provider, field name, age, and TTL. |
| No cascade | Cache eviction does not trigger immediate refetch. The next scheduled scan cycle will naturally repopulate the cache. |
| Minimum retention | Cache entries younger than 60 seconds are never evicted, regardless of TTL. This prevents thrashing during rapid scan cycles. |

**Implementation:**

```python
@dataclass
class CacheEvictionAction:
    action_type: str = "HEAL-ART-EVICT"
    evicted_keys: list[str] = field(default_factory=list)
    MIN_RETENTION_SECONDS: int = 60

    def execute(self) -> HealResult:
        cache = get_provenance_cache()
        evicted = []

        for key, record in list(cache.items()):
            age = (now() - record.as_of).total_seconds()

            if age < self.MIN_RETENTION_SECONDS:
                continue

            if age > record.ttl_seconds:
                evicted.append({
                    "key": key,
                    "provider": record.provider,
                    "field": record.field,
                    "age_seconds": age,
                    "ttl_seconds": record.ttl_seconds
                })
                cache.pop(key)

        self.evicted_keys = [e["key"] for e in evicted]

        return HealResult(
            success=True,
            code=f"HEAL_ART_EVICT_OK_{len(evicted)}_entries",
            details={"evicted": evicted}
        )
```

---

### 2.6 Reconnect to Telegram API

**Action ID Prefix:** `HEAL-TG`

**Trigger:** Telegram `sendMessage` API call returns a network error (connection refused, timeout, DNS failure) or HTTP 5xx.

**Scope:** Re-establish the HTTPS connection to the Telegram Bot API. Retry sending the failed message.

**Safety Constraints:**

| Constraint | Rule |
|---|---|
| Exponential backoff | Retry intervals: 2s, 4s, 8s, 16s, 32s, 60s (cap). Each retry interval is doubled from the previous. |
| Max retries | Maximum 6 retries per connection failure event (total ~2 minutes of retrying). After 6 failures, stop retrying and escalate. |
| Message queue preservation | Failed messages are held in a bounded queue (max 50 messages). When queue is full, oldest messages are dropped with logging. |
| No message modification | The reconnection logic MUST NOT modify, duplicate, or reorder messages. Messages are retried in FIFO order. |
| Cooldown after escalation | After escalation, no further auto-reconnect attempts for 10 minutes. Operator must acknowledge or the system retries after the cooldown. |

**Implementation:**

```python
@dataclass
class TelegramReconnectAction:
    action_type: str = "HEAL-TG-RECONNECT"
    retry_count: int = 0
    MAX_RETRIES: int = 6
    BASE_DELAY: float = 2.0
    MAX_DELAY: float = 60.0
    COOLDOWN_SECONDS: int = 600
    last_escalation_at: datetime = None

    def execute(self, failed_message: dict) -> HealResult:
        if self.last_escalation_at:
            elapsed = (now() - self.last_escalation_at).total_seconds()
            if elapsed < self.COOLDOWN_SECONDS:
                return HealResult(success=False, code="HEAL_TG_COOLDOWN")

        for attempt in range(self.MAX_RETRIES):
            delay = min(
                self.BASE_DELAY * (2 ** attempt),
                self.MAX_DELAY
            )
            time.sleep(delay)

            try:
                result = telegram_send(failed_message)
                if result.ok:
                    self.retry_count = 0
                    return HealResult(
                        success=True,
                        code=f"HEAL_TG_RECONNECT_OK_attempt_{attempt + 1}"
                    )
            except Exception as e:
                log.warning(f"HEAL-TG retry {attempt + 1}/{self.MAX_RETRIES}: {e}")
                continue

        # All retries exhausted
        self.last_escalation_at = now()
        escalate_to_operator("HEAL_TG_ALL_RETRIES_EXHAUSTED")
        return HealResult(success=False, code="HEAL_TG_EXHAUSTED")
```

---

## 3. Requires Human Approval

The following actions are NEVER performed automatically. Any code path that attempts to execute these without operator confirmation is a specification violation.

| # | Action | Why It Requires Approval | Escalation Channel |
|---|---|---|---|
| 3.1 | **Changing thresholds** (confidence, ATR multiplier, RVOL floor, score floor, magnitude gate) | Threshold changes directly affect risk exposure and signal quality. A miscalibrated threshold can cause capital loss or missed opportunities. | Telegram `[SYSTEM]` alert with current and proposed values |
| 3.2 | **Changing the core universe** (add/remove tickers from ISA fund list) | Universe changes alter the trading surface. Adding a volatile or illiquid ticker introduces unvetted risk. Removing a ticker may strand open positions. | Telegram `[SYSTEM]` alert with proposed change |
| 3.3 | **Disabling data health gates** (staleness gate, completeness gate, OHLC integrity gate, any sanity gate) | Disabling a gate removes a safety boundary. The system may publish corrupt, stale, or nonsensical data. | Telegram `[SYSTEM]` alert + War Room banner |
| 3.4 | **Modifying feature flags** (enabling/disabling strategies, enabling/disabling output channels, toggling dark pool integration) | Feature flags change system behaviour in ways that affect trading output. | Telegram `[SYSTEM]` alert |
| 3.5 | **Restarting the trading engine** (`main.py` orchestrator, APScheduler, scan loop) | Engine restart disrupts the scan cycle, clears in-memory state, and triggers quiet mode. Must be a deliberate operator decision. | Telegram `[SYSTEM]` alert |
| 3.6 | **Any Docker container restart/rebuild** (`docker-compose restart`, `docker-compose build`, `docker-compose up -d`) | Container operations affect the entire stack. A bad rebuild can introduce regressions. A restart clears all in-memory state. | Telegram `[SYSTEM]` alert + SSH notification |
| 3.7 | **Kill switch activation/deactivation** (master output suppression toggle) | The kill switch is the system's emergency brake. Activating it stops all outputs. Deactivating it resumes them. Both are high-consequence actions. | Telegram `[SYSTEM]` alert |
| 3.8 | **Strategy enable/disable** (S15, S3, or any future strategy) | Strategies define what the system trades and how. Enabling or disabling a strategy changes the system's trading behaviour. | Telegram `[SYSTEM]` alert |

### Enforcement Mechanism

```python
REQUIRES_HUMAN_APPROVAL = frozenset([
    "threshold_change",
    "universe_change",
    "gate_disable",
    "feature_flag_change",
    "engine_restart",
    "docker_operation",
    "kill_switch_toggle",
    "strategy_toggle",
])

def guard_human_approval(action_type: str) -> None:
    """
    Called before any action execution. Raises if the action
    requires human approval and no approval token is present.
    """
    if action_type in REQUIRES_HUMAN_APPROVAL:
        raise HumanApprovalRequired(
            f"Action '{action_type}' requires operator approval. "
            f"Use POST /api/approve/{action_type} with API key and reason."
        )
```

### Approval API

```
POST /api/approve/{action_type}
Headers:
    X-API-Key: {operator_api_key}
    Content-Type: application/json
Body:
{
    "action_type": "threshold_change",
    "details": {"param": "confidence_floor", "old": 50, "new": 45},
    "reason": "string (mandatory, min 10 characters)"
}
Response:
{
    "approval_token": "uuid",
    "expires_at": "ISO8601 (5 minutes from issuance)",
    "action_type": "threshold_change"
}
```

Approval tokens expire after 5 minutes. Expired tokens cannot be used. Every approval is logged to the immutable audit trail.

---

## 4. Action Logging

Every auto-action -- whether successful, failed, or skipped -- MUST produce a structured log record. No auto-action may execute without generating this record.

### 4.1 Action Log Record Schema

```json
{
    "action_id": "uuid4",
    "action_type": "HEAL-WR-RESTART",
    "timestamp": "2026-02-27T14:32:18Z",
    "trigger_reason": "War Room health check failed 3 consecutive times",
    "trigger_evidence": {
        "failure_1": {"timestamp": "2026-02-27T14:31:00Z", "http_status": null, "error": "ConnectionRefused"},
        "failure_2": {"timestamp": "2026-02-27T14:31:12Z", "http_status": null, "error": "ConnectionRefused"},
        "failure_3": {"timestamp": "2026-02-27T14:31:24Z", "http_status": null, "error": "ConnectionRefused"}
    },
    "evidence_snapshot": {
        "before": {
            "war_room_pid": 12345,
            "war_room_status": "UNRESPONSIVE",
            "last_successful_health_check": "2026-02-27T14:29:55Z"
        },
        "after": {
            "war_room_pid": 12389,
            "war_room_status": "HEALTHY",
            "health_check_response_ms": 42
        }
    },
    "result": {
        "success": true,
        "code": "HEAL_WR_RESTART_OK",
        "duration_ms": 2150
    },
    "reversible": true,
    "reversal_steps": [
        "Kill new War Room process (PID 12389)",
        "Investigate root cause of original failure",
        "Restart War Room manually with debug flags if needed"
    ],
    "operator_notified": false,
    "escalated": false
}
```

### 4.2 Required Fields

| Field | Type | Description | Mandatory |
|---|---|---|---|
| `action_id` | `uuid4` | Unique identifier for this action instance. Generated at action start. | Yes |
| `action_type` | `string` | Action ID prefix from Section 2 (e.g., `HEAL-WR-RESTART`, `HEAL-CFG-RELOAD`). | Yes |
| `timestamp` | `ISO8601` | UTC timestamp of action execution. | Yes |
| `trigger_reason` | `string` | Human-readable description of what triggered this action. | Yes |
| `trigger_evidence` | `object` | Raw evidence that triggered the action (failure counts, timestamps, error messages). | Yes |
| `evidence_snapshot.before` | `object` | System state snapshot taken immediately BEFORE the action executes. | Yes |
| `evidence_snapshot.after` | `object` | System state snapshot taken immediately AFTER the action executes. | Yes |
| `result.success` | `bool` | Whether the action completed successfully. | Yes |
| `result.code` | `string` | Machine-readable result code. | Yes |
| `result.duration_ms` | `int` | Wall-clock time for the action to execute, in milliseconds. | Yes |
| `reversible` | `bool` | Whether this action can be reversed. All allowed auto-actions MUST be reversible. | Yes |
| `reversal_steps` | `list[string]` | Ordered list of steps an operator would take to reverse this action. | Yes |
| `operator_notified` | `bool` | Whether the operator was sent a notification about this action. | Yes |
| `escalated` | `bool` | Whether this action was escalated due to failure. | Yes |

### 4.3 Log Storage

| Destination | Format | Retention |
|---|---|---|
| `logs/self_healing.log` | JSON lines (one record per line) | 90 days (rotated at 10 MB) |
| `artifacts/heal_actions.json` | JSON array of last 100 actions | Overwritten per cycle |
| War Room `/api/heal/history` | JSON via API | In-memory, last 200 actions |

---

## 5. Escalation Policy

When an auto-action fails, the system follows a strict escalation ladder. No step in this ladder may be skipped.

### 5.1 Escalation Ladder

```
Step 1: AUTO-ACTION FAILS
    |
    v
Step 2: RETRY ONCE (same action, same parameters)
    |-- Wait: 30 seconds before retry
    |-- Log: retry attempt with action_id reference
    |
    |-- If retry succeeds:
    |       Log success. Resume normal operation. END.
    |
    v (retry also fails)
Step 3: ESCALATE TO OPERATOR
    |-- Send Telegram [SYSTEM] alert:
    |       "[SYSTEM] HEAL ESCALATION: {action_type} failed after retry.
    |        Trigger: {trigger_reason}
    |        Last error: {error_message}
    |        Action ID: {action_id}"
    |
    v
Step 4: BLOCK AFFECTED OUTPUT CHANNEL
    |-- Identify the output channel affected by the failed heal:
    |       HEAL-WR-*    -> Block War Room WebSocket pushes
    |       HEAL-TG-*    -> Block Telegram sends
    |       HEAL-CFG-*   -> Block all outputs (config is global)
    |       HEAL-ART-*   -> No block (cache eviction is non-critical)
    |       HEAL-RDY-*   -> No block (readiness is informational)
    |       HEAL-LOG-*   -> No block (log rotation is non-critical)
    |-- Set channel status to BLOCKED_PENDING_OPERATOR
    |-- Log the block with action_id reference
    |
    v
Step 5: AWAIT OPERATOR
    |-- System continues operating with blocked channel
    |-- Readiness checks continue reporting the failure
    |-- Operator resolves via:
    |       POST /api/heal/acknowledge/{action_id}
    |       (requires API key + reason)
    |-- On acknowledgement:
    |       Unblock channel
    |       Reset failure counters
    |       Log acknowledgement
```

### 5.2 Escalation Telegram Message Format

```
[SYSTEM] HEAL ESCALATION

Action: HEAL-WR-RESTART
Status: FAILED (retry exhausted)
Trigger: War Room health check failed 3x
Error: Process restart returned non-zero exit code
Action ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890

Affected channel: War Room (BLOCKED)
Operator action required: POST /api/heal/acknowledge/{action_id}
```

### 5.3 Escalation Timeout

If no operator acknowledgement is received within 30 minutes:

1. Send a second Telegram alert: `[SYSTEM] HEAL REMINDER: {action_type} still unresolved. Channel blocked for {elapsed_minutes} minutes.`
2. Repeat reminder every 30 minutes.
3. After 2 hours with no acknowledgement, send a final `[SYSTEM] HEAL CRITICAL: Unresolved heal failure for 2 hours. Manual intervention required.`
4. No further automatic escalation. The channel remains blocked until the operator acts.

---

## 6. Self-Healing Controller Interface

### 6.1 Module Location

```
core/self_healing.py
```

### 6.2 Public Interface

```python
class SelfHealingController:
    """
    Central controller for all self-healing operations.
    Instantiated once in main.py. Called from APScheduler jobs
    and error handlers.
    """

    def __init__(self, config: dict, telegram: TelegramClient, scheduler: APScheduler):
        self.config = config
        self.telegram = telegram
        self.scheduler = scheduler
        self.action_log = ActionLog("logs/self_healing.log")
        self.failure_counters = FailureCounters()
        self.escalation_state = EscalationState()

    def handle_war_room_failure(self, error: Exception) -> HealResult:
        """Called when War Room health check fails."""
        ...

    def handle_telegram_failure(self, error: Exception, message: dict) -> HealResult:
        """Called when Telegram send fails."""
        ...

    def handle_config_change(self, event: FileChangeEvent) -> HealResult:
        """Called when settings.yaml is modified."""
        ...

    def run_readiness_checks(self) -> HealResult:
        """Called every 5 minutes by APScheduler."""
        ...

    def evict_stale_cache(self) -> HealResult:
        """Called every 5 minutes by APScheduler."""
        ...

    def rotate_logs(self) -> None:
        """Handled automatically by RotatingFileHandler. No custom code."""
        pass

    def get_heal_history(self, limit: int = 100) -> list[dict]:
        """Returns recent heal actions for War Room display."""
        ...

    def acknowledge(self, action_id: str, operator_reason: str) -> bool:
        """Operator acknowledges a failed heal action. Unblocks channel."""
        ...
```

### 6.3 APScheduler Registration

```python
# In main.py, after engine initialisation:

healer = SelfHealingController(config, telegram, scheduler)

scheduler.add_job(
    healer.run_readiness_checks,
    trigger="interval",
    minutes=5,
    id="heal_readiness",
    name="Self-Healing Readiness Checks"
)

scheduler.add_job(
    healer.evict_stale_cache,
    trigger="interval",
    minutes=5,
    id="heal_cache_eviction",
    name="Self-Healing Cache Eviction"
)
```

### 6.4 War Room API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/heal/status` | GET | Current self-healing status: active heals, blocked channels, failure counters |
| `GET /api/heal/history` | GET | Last 200 heal actions with full audit records |
| `POST /api/heal/acknowledge/{action_id}` | POST | Operator acknowledges a failed heal. Requires API key + reason. |
| `GET /api/heal/config` | GET | Current self-healing configuration (allowed actions, thresholds, cooldowns) |

---

## 7. Acceptance Tests

### T-HEAL-001: Config Reload on File Change

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running with valid `config/settings.yaml`. |
| **Action** | Modify a non-threshold parameter in `settings.yaml` (e.g., change `log_level` from `INFO` to `DEBUG`). |
| **Expected** | Config reloaded within 10 seconds. Action log contains `HEAL-CFG-RELOAD` entry with diff showing the changed key. Log level changes to `DEBUG`. Previous config retained in rollback buffer for 60 seconds. |
| **Pass Criteria** | Action log entry present with `success: true`, diff is accurate, new config is active, rollback buffer populated. |

### T-HEAL-002: Config Reload Rejected on Schema Violation

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running with valid config. |
| **Action** | Write invalid YAML to `settings.yaml` (e.g., remove a required key, set a string where int is expected). |
| **Expected** | Reload attempted but rejected. Previous config retained. Action log contains `HEAL_CFG_SCHEMA_FAIL`. No disruption to engine operation. |
| **Pass Criteria** | Action log entry present with `success: false`, `code: HEAL_CFG_SCHEMA_FAIL`. Engine continues with previous config. |

### T-HEAL-003: War Room Auto-Restart After 3 Health Check Failures

| Aspect | Detail |
|---|---|
| **Precondition** | Engine and War Room running normally. |
| **Action** | Kill the War Room process manually (`kill -STOP <pid>` to simulate hang). Wait for 3 consecutive health check failures (minimum 30 seconds). |
| **Expected** | After 3rd failure, system auto-restarts the War Room process. New PID assigned. Health check passes on next probe. Action log contains `HEAL-WR-RESTART` with before/after PIDs. |
| **Pass Criteria** | War Room responsive within 60 seconds of 3rd failure. Action log entry with `success: true`. Cooldown timer started (15 min). |

### T-HEAL-004: War Room Restart Cooldown Enforcement

| Aspect | Detail |
|---|---|
| **Precondition** | War Room was auto-restarted less than 15 minutes ago. |
| **Action** | Kill the War Room process again. Wait for 3 health check failures. |
| **Expected** | System does NOT auto-restart (cooldown active). Escalation triggered: Telegram `[SYSTEM]` alert sent to operator. Channel status set to `BLOCKED_PENDING_OPERATOR`. |
| **Pass Criteria** | No new War Room process spawned. Telegram escalation message sent. Action log contains `HEAL_WR_COOLDOWN_ACTIVE`. |

### T-HEAL-005: War Room Daily Restart Limit

| Aspect | Detail |
|---|---|
| **Precondition** | War Room has been auto-restarted 3 times in the current 24-hour window. |
| **Action** | Trigger a 4th War Room failure sequence. |
| **Expected** | System refuses the 4th restart. Escalates to operator with `DAILY_LIMIT_REACHED`. |
| **Pass Criteria** | Action log contains `HEAL_WR_DAILY_LIMIT_REACHED`. Telegram escalation sent. No restart executed. |

### T-HEAL-006: Telegram Reconnect with Exponential Backoff

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running. Telegram Bot API endpoint unreachable (simulate by blocking outbound HTTPS to `api.telegram.org`). |
| **Action** | Trigger a Telegram send. Observe retry behaviour. |
| **Expected** | System retries with backoff: 2s, 4s, 8s, 16s, 32s, 60s. After 6 failures (~2 minutes), stops retrying. Escalation sent via fallback channel (logged to `logs/self_healing.log` since Telegram is down). Failed message held in queue (not lost). |
| **Pass Criteria** | Exactly 6 retry attempts logged with increasing delays. Message preserved in queue. Escalation logged. |

### T-HEAL-007: Stale Cache Eviction

| Aspect | Detail |
|---|---|
| **Precondition** | Provenance cache contains entries with varying ages. At least 3 entries have `age > ttl_seconds`. At least 1 entry has `age < 60s` (minimum retention). |
| **Expected** | Entries with `age > ttl_seconds AND age >= 60s` are evicted. Entry with `age < 60s` is retained regardless of TTL. Action log contains `HEAL-ART-EVICT` with count of evicted entries and their details. |
| **Pass Criteria** | Correct entries evicted. Minimum retention respected. Eviction details logged. Cache size reduced by expected count. |

### T-HEAL-008: Escalation Ladder Full Traversal

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running. |
| **Action** | Trigger a War Room failure. Make the auto-restart fail (e.g., corrupt the War Room startup script). |
| **Expected** | Step 1: Auto-action fails. Step 2: Retry after 30s, also fails. Step 3: Telegram `[SYSTEM]` alert sent. Step 4: War Room channel blocked. Step 5: System awaits operator. Reminder sent at 30-minute intervals. |
| **Pass Criteria** | All 5 escalation steps executed in order. Telegram messages sent at correct intervals. War Room channel status is `BLOCKED_PENDING_OPERATOR`. Action log contains complete escalation trail. |

### Acceptance Test Summary

| Test ID | Category | Auto-Action Tested | Key Assertion |
|---|---|---|---|
| T-HEAL-001 | Config | Reload | Successful reload with diff logging |
| T-HEAL-002 | Config | Reload (rejection) | Schema violation rejected, previous config retained |
| T-HEAL-003 | War Room | Restart | 3-failure gate, successful restart |
| T-HEAL-004 | War Room | Restart (cooldown) | Cooldown prevents premature re-restart |
| T-HEAL-005 | War Room | Restart (daily limit) | Daily cap enforced, escalation on breach |
| T-HEAL-006 | Telegram | Reconnect | Exponential backoff, message preservation |
| T-HEAL-007 | Cache | Eviction | TTL-based eviction with minimum retention |
| T-HEAL-008 | Escalation | Full ladder | All 5 steps execute in order |

---

## 8. Proof Artifacts

### 8.1 Log Files

| Artifact | Location | Format | Retention |
|---|---|---|---|
| Self-healing action log | `logs/self_healing.log` | JSON lines | 90 days, rotated at 10 MB |
| Readiness status | `artifacts/readiness_status.json` | JSON | Overwritten every 5 minutes |
| Recent heal actions | `artifacts/heal_actions.json` | JSON array | Last 100 actions |
| Escalation audit | `logs/escalation_audit.log` | JSON lines | 90 days |

### 8.2 Monitoring Metrics

| Metric | Description | Alert Threshold |
|---|---|---|
| `heal_actions_total` | Counter of all heal actions by type | Informational |
| `heal_actions_failed` | Counter of failed heal actions by type | Alert if > 3/hour for any type |
| `heal_escalations_total` | Counter of operator escalations | Alert if > 1/hour |
| `heal_war_room_restarts_daily` | War Room restarts in rolling 24h window | Alert if >= 3 (approaching limit) |
| `heal_telegram_retries` | Telegram reconnect retries in rolling 1h | Alert if > 12/hour |
| `heal_cache_evictions` | Cache entries evicted per cycle | Informational |
| `heal_channel_blocked` | Boolean per channel: is any channel currently blocked pending operator | Alert immediately on any block |

### 8.3 Compliance Verification

```bash
# Verify self-healing controller is registered
docker exec nzt48 python -c "from core.self_healing import SelfHealingController; print('OK')"

# Verify APScheduler jobs are registered
docker exec nzt48 python -c "
from apscheduler.schedulers.background import BackgroundScheduler
import json
# Check heal_readiness and heal_cache_eviction jobs exist
"

# Verify action log is being written
docker exec nzt48 tail -5 logs/self_healing.log

# Verify readiness status artifact exists
docker exec nzt48 cat artifacts/readiness_status.json | python -m json.tool

# Run acceptance test suite
docker exec nzt48 pytest tests/test_self_healing.py -v --tb=short
```

---

## 9. Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-02-27 | NZT-48 System | Initial specification -- all 6 auto-actions defined, human-approval boundary established, escalation policy defined |

---

**END OF DOCUMENT**

This specification is binding. Any auto-action not listed in Section 2 is prohibited. Any action listed in Section 3 that executes without operator approval is a specification violation. No exceptions.
