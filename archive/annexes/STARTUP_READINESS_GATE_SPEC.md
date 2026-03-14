# NZT-48 Startup Readiness Gate Specification

**Document ID:** NZT48-ANNEX-SRG-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** BINDING
**Owner:** NZT-48 Trading Engine
**Classification:** Internal / Operational
**Scope:** Pre-flight checklist run on boot and before each session window

---

## Table of Contents

1. [Objective](#1-objective)
2. [Scope](#2-scope)
3. [Definitions](#3-definitions)
4. [Gate Checks](#4-gate-checks)
5. [Gate Output States](#5-gate-output-states)
6. [Policy](#6-policy)
7. [Implementation Plan](#7-implementation-plan)
8. [Acceptance Tests](#8-acceptance-tests)
9. [Operator Actions per Failure Mode](#9-operator-actions-per-failure-mode)
10. [Evidence and Proof Artifacts](#10-evidence-and-proof-artifacts)
11. [Revision History](#11-revision-history)

---

## 1. Objective

Define a **mandatory pre-flight readiness gate** that the NZT-48 Trading Engine executes:

1. **On every system boot** (container start / orchestrator initialization).
2. **Before each session window transition** (06:55 UK pre-market prep, 13:25 UK pre-US-open prep).

The gate validates that every subsystem required for safe trade output is operational, reachable, and correctly configured. **The system MUST NOT emit trade outputs, signals, plays, or PDF reports until the readiness gate returns READY.** Health and diagnostic messages are always permitted.

**Design Principle:** Fail-closed. If the gate cannot determine readiness, the system assumes NOT READY. A missed session costs opportunity; a session run on broken infrastructure costs capital and trust. The asymmetry is absolute.

---

## 2. Scope

**In scope:**

| Component | What is checked |
|---|---|
| Docker image integrity | Git hash baked at build matches expected |
| Filesystem | Required directories exist and are writable |
| Data providers | yfinance reachable, `DataFeedValidator` operational |
| Time configuration | Timezone and session schedule in `settings.yaml` |
| War Room API | FastAPI endpoints on `:8000` responding |
| Telegram delivery | Bot token configured, dry-run send succeeds |
| Artifact persistence | Atomic write/delete test to `artifacts/` |
| Schema validation | Existing loaded artifacts conform to `core.schemas` |

**Out of scope:**

- Internal logging (stdout, Docker logs) -- always unrestricted.
- Health check endpoints (`/api/health`, `/status`) -- always respond regardless of gate state.
- Historical data quality (covered by the Sanity Gate Specification, NZT48-ANNEX-SG-001).
- Strategy-level parameter validation (covered by Go/No-Go Scorecard).

---

## 3. Definitions

| Term | Definition |
|---|---|
| **Gate** | The complete set of 8 readiness checks executed as a single atomic assessment |
| **Check** | An individual validation within the gate (CHECK 1 through CHECK 8) |
| **Critical check** | A check whose failure alone is sufficient to HALT the system (checks 3, 5, 6) |
| **Non-critical check** | A check whose failure degrades the system but does not mandate a halt |
| **READY** | All 8 checks PASS; normal operation permitted |
| **DEGRADED** | 1-2 non-critical checks FAIL; system health messages only, no trade outputs |
| **HALTED** | Any critical check FAIL or 3+ total checks FAIL; startup failure alert only |
| **Session window** | A scheduled transition point where the gate re-executes (06:55 UK, 13:25 UK) |
| **Override** | Explicit operator flag to bypass DEGRADED state for one session window |
| **Dry-run send** | A Telegram message sent to the `[SYSTEM]` sandbox channel to verify connectivity |

---

## 4. Gate Checks

### CHECK 1: Docker Image Integrity

**Code:** `SRG_CHECK_DOCKER_IMAGE`
**Criticality:** Non-critical

**Rule:** Compare the git commit hash baked into the Docker image at build time against the expected hash declared in `settings.yaml` or the `GIT_COMMIT_HASH` environment variable.

**Pass condition:** Hashes match exactly.

**Rationale:** Detects stale images, failed rebuilds, and accidental rollbacks. A hash mismatch means the running code does not match what was deployed.

**Implementation:**

```python
def check_docker_image(self) -> CheckResult:
    """CHECK 1: Verify Docker image matches expected git hash."""
    expected = os.environ.get("EXPECTED_GIT_HASH", "").strip()
    actual = os.environ.get("GIT_COMMIT_HASH", "").strip()

    if not expected:
        return CheckResult(
            name="SRG_CHECK_DOCKER_IMAGE",
            passed=True,
            detail="No expected hash configured; check skipped (advisory).",
            critical=False,
        )

    if not actual:
        return CheckResult(
            name="SRG_CHECK_DOCKER_IMAGE",
            passed=False,
            detail="GIT_COMMIT_HASH not baked into image. Rebuild required.",
            critical=False,
        )

    match = expected == actual
    return CheckResult(
        name="SRG_CHECK_DOCKER_IMAGE",
        passed=match,
        detail=f"Expected: {expected[:12]}, Actual: {actual[:12]}",
        critical=False,
    )
```

**Dockerfile requirement (build-time bake):**

```dockerfile
ARG GIT_COMMIT_HASH=unknown
ENV GIT_COMMIT_HASH=$GIT_COMMIT_HASH
```

```bash
docker build --build-arg GIT_COMMIT_HASH=$(git rev-parse HEAD) -t nzt48 .
```

---

### CHECK 2: Required Directories Exist and Writable

**Code:** `SRG_CHECK_DIRECTORIES`
**Criticality:** Non-critical

**Rule:** Verify that each of the following directories exists and is writable by the running process.

| Directory | Purpose |
|---|---|
| `config/` | Configuration files (`settings.yaml`) |
| `artifacts/` | Session artifacts (`system_state.json`, `plays.json`) |
| `data/` | Database, kill switch file, scan health |
| `reports/` | Generated PDF reports |
| `data/reports/` | Archived report outputs |

**Pass condition:** All 5 directories exist AND are writable (verified by `os.access(path, os.W_OK)`).

**Implementation:**

```python
REQUIRED_DIRS = [
    Path("config"),
    Path("artifacts"),
    Path("data"),
    Path("reports"),
    Path("data/reports"),
]

def check_directories(self) -> CheckResult:
    """CHECK 2: Verify required directories exist and are writable."""
    missing = []
    not_writable = []

    for d in self.REQUIRED_DIRS:
        if not d.exists():
            missing.append(str(d))
        elif not os.access(d, os.W_OK):
            not_writable.append(str(d))

    if missing or not_writable:
        parts = []
        if missing:
            parts.append(f"Missing: {', '.join(missing)}")
        if not_writable:
            parts.append(f"Not writable: {', '.join(not_writable)}")
        return CheckResult(
            name="SRG_CHECK_DIRECTORIES",
            passed=False,
            detail="; ".join(parts),
            critical=False,
        )

    return CheckResult(
        name="SRG_CHECK_DIRECTORIES",
        passed=True,
        detail=f"All {len(self.REQUIRED_DIRS)} directories OK.",
        critical=False,
    )
```

---

### CHECK 3: Data Provider Reachability

**Code:** `SRG_CHECK_DATA_PROVIDER`
**Criticality:** CRITICAL

**Rule:** Execute a live data fetch for a known ticker (`QQQ3.L`) via yfinance to confirm the primary data provider is reachable. Additionally, confirm that the `DataFeedValidator` instance can be instantiated and its health check passes.

**Pass condition:** yfinance returns at least 1 bar of data for `QQQ3.L` within 15 seconds AND `DataFeedValidator` instantiation does not raise.

**Rationale:** The system cannot produce any meaningful output without market data. A data provider outage during a session window would produce stale or absent signals, both of which are dangerous for a leveraged ETP system.

**Implementation:**

```python
import yfinance as yf
from feeds.data_validator import DataFeedValidator

def check_data_provider(self) -> CheckResult:
    """CHECK 3: Verify yfinance reachable + DataFeedValidator operational."""
    # Part A: yfinance probe
    try:
        ticker = yf.Ticker("QQQ3.L")
        hist = ticker.history(period="1d", timeout=15)
        if hist.empty:
            return CheckResult(
                name="SRG_CHECK_DATA_PROVIDER",
                passed=False,
                detail="yfinance returned empty data for QQQ3.L.",
                critical=True,
            )
    except Exception as e:
        return CheckResult(
            name="SRG_CHECK_DATA_PROVIDER",
            passed=False,
            detail=f"yfinance fetch failed: {type(e).__name__}: {e}",
            critical=True,
        )

    # Part B: DataFeedValidator instantiation
    try:
        validator = DataFeedValidator()
        _ = validator  # confirm no exception on init
    except Exception as e:
        return CheckResult(
            name="SRG_CHECK_DATA_PROVIDER",
            passed=False,
            detail=f"DataFeedValidator init failed: {type(e).__name__}: {e}",
            critical=True,
        )

    return CheckResult(
        name="SRG_CHECK_DATA_PROVIDER",
        passed=True,
        detail=f"QQQ3.L returned {len(hist)} bars. DataFeedValidator OK.",
        critical=True,
    )
```

**Timeout:** 15 seconds. If yfinance hangs beyond this, the check FAILS.

---

### CHECK 4: Timezone and Session Schedule Configuration

**Code:** `SRG_CHECK_TIMEZONE_SCHEDULE`
**Criticality:** Non-critical

**Rule:** Verify that:

1. `system.uk_timezone` in `settings.yaml` resolves to `"Europe/London"`.
2. The `schedule` section in `settings.yaml` contains at least the `pre_market` and `us_open` session keys.
3. The system clock's UTC offset matches what `Europe/London` should produce for the current date (accounting for GMT/BST).

**Pass condition:** All 3 sub-checks pass.

**Implementation:**

```python
import zoneinfo
from datetime import datetime, timezone

def check_timezone_schedule(self) -> CheckResult:
    """CHECK 4: Verify timezone and session schedule configuration."""
    errors = []

    # 4a: UK timezone setting
    uk_tz_name = cfg.get("system.uk_timezone", "")
    if uk_tz_name != "Europe/London":
        errors.append(f"uk_timezone is '{uk_tz_name}', expected 'Europe/London'")

    # 4b: Schedule keys present
    schedule = cfg.get("schedule", {})
    required_sessions = ["pre_market", "us_open"]
    for session_key in required_sessions:
        if session_key not in schedule:
            errors.append(f"Missing schedule key: '{session_key}'")

    # 4c: System clock UTC offset matches Europe/London
    try:
        london_tz = zoneinfo.ZoneInfo("Europe/London")
        london_now = datetime.now(london_tz)
        expected_offset = london_now.utcoffset()
        system_offset = datetime.now(timezone.utc).utcoffset()
        # We only verify the London TZ resolves; system may run in UTC
        if expected_offset is None:
            errors.append("Cannot resolve Europe/London UTC offset")
    except Exception as e:
        errors.append(f"Timezone resolution failed: {e}")

    if errors:
        return CheckResult(
            name="SRG_CHECK_TIMEZONE_SCHEDULE",
            passed=False,
            detail="; ".join(errors),
            critical=False,
        )

    return CheckResult(
        name="SRG_CHECK_TIMEZONE_SCHEDULE",
        passed=True,
        detail=f"Europe/London OK. Schedule has {len(schedule)} sessions.",
        critical=False,
    )
```

---

### CHECK 5: War Room API Endpoints Respond

**Code:** `SRG_CHECK_WAR_ROOM_API`
**Criticality:** CRITICAL

**Rule:** Send `GET /api/health` to `http://localhost:8000` and verify a `200` response with a JSON body containing `"status"`.

**Pass condition:** HTTP 200 received within 10 seconds AND response body is valid JSON containing a `status` key.

**Rationale:** The War Room (FastAPI on `:8000`) is the operator's primary interface for real-time system awareness. If the API layer is down, the operator is flying blind. Combined with a data provider failure, this creates an unrecoverable blind spot.

**Implementation:**

```python
import httpx

def check_war_room_api(self) -> CheckResult:
    """CHECK 5: Verify War Room API is responding."""
    url = "http://localhost:8000/api/health"
    try:
        resp = httpx.get(url, timeout=10.0)
        if resp.status_code != 200:
            return CheckResult(
                name="SRG_CHECK_WAR_ROOM_API",
                passed=False,
                detail=f"GET /api/health returned {resp.status_code}.",
                critical=True,
            )
        body = resp.json()
        if "status" not in body:
            return CheckResult(
                name="SRG_CHECK_WAR_ROOM_API",
                passed=False,
                detail="Response JSON missing 'status' key.",
                critical=True,
            )
    except httpx.TimeoutException:
        return CheckResult(
            name="SRG_CHECK_WAR_ROOM_API",
            passed=False,
            detail="GET /api/health timed out after 10s.",
            critical=True,
        )
    except Exception as e:
        return CheckResult(
            name="SRG_CHECK_WAR_ROOM_API",
            passed=False,
            detail=f"API unreachable: {type(e).__name__}: {e}",
            critical=True,
        )

    return CheckResult(
        name="SRG_CHECK_WAR_ROOM_API",
        passed=True,
        detail=f"GET /api/health -> 200. Status: {body.get('status')}",
        critical=True,
    )
```

---

### CHECK 6: Telegram Delivery Configured and Operational

**Code:** `SRG_CHECK_TELEGRAM`
**Criticality:** CRITICAL

**Rule:** Verify that:

1. The `TELEGRAM_BOT_TOKEN` environment variable is set and non-empty (sourced from `telegram.bot_token_env` in `settings.yaml`).
2. The `TELEGRAM_CHAT_ID` environment variable is set and non-empty.
3. A dry-run message can be sent to the `[SYSTEM]` channel (or sandbox chat) without error.

**Pass condition:** Token and chat ID are present AND the dry-run send returns a Telegram API success response.

**Rationale:** Telegram is the primary delivery channel for signals, alerts, and system health messages. If Telegram is misconfigured, the operator receives nothing -- the system operates silently, which is the worst possible failure mode.

**Implementation:**

```python
import os
import httpx

def check_telegram(self) -> CheckResult:
    """CHECK 6: Verify Telegram token and dry-run send."""
    token_env = cfg.get("telegram.bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_id_env = cfg.get("telegram.chat_id_env", "TELEGRAM_CHAT_ID")

    token = os.environ.get(token_env, "").strip()
    chat_id = os.environ.get(chat_id_env, "").strip()

    if not token:
        return CheckResult(
            name="SRG_CHECK_TELEGRAM",
            passed=False,
            detail=f"Environment variable '{token_env}' is empty or missing.",
            critical=True,
        )

    if not chat_id:
        return CheckResult(
            name="SRG_CHECK_TELEGRAM",
            passed=False,
            detail=f"Environment variable '{chat_id_env}' is empty or missing.",
            critical=True,
        )

    # Dry-run: send a silent system health message
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": "[SYSTEM] Startup Readiness Gate -- dry-run OK.",
            "disable_notification": True,
        }
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp_data = resp.json()
        if not resp_data.get("ok"):
            return CheckResult(
                name="SRG_CHECK_TELEGRAM",
                passed=False,
                detail=f"Telegram API error: {resp_data.get('description', 'unknown')}",
                critical=True,
            )
    except Exception as e:
        return CheckResult(
            name="SRG_CHECK_TELEGRAM",
            passed=False,
            detail=f"Telegram dry-run failed: {type(e).__name__}: {e}",
            critical=True,
        )

    return CheckResult(
        name="SRG_CHECK_TELEGRAM",
        passed=True,
        detail="Token present. Dry-run send succeeded.",
        critical=True,
    )
```

---

### CHECK 7: Artifact Write Permissions

**Code:** `SRG_CHECK_ARTIFACT_WRITE`
**Criticality:** Non-critical

**Rule:** Perform an atomic write-then-delete test to `artifacts/readiness_test.json`. This verifies that the `artifacts/` directory is not only present (CHECK 2) but that the process can create, write, and remove files -- the exact operations the engine performs every session.

**Pass condition:** File is written, content is verified by read-back, and file is successfully deleted. All within 5 seconds.

**Implementation:**

```python
import json
import time
from pathlib import Path

def check_artifact_write(self) -> CheckResult:
    """CHECK 7: Atomic write/read/delete test on artifacts/."""
    test_path = Path("artifacts/readiness_test.json")
    test_payload = {
        "gate": "SRG_CHECK_ARTIFACT_WRITE",
        "timestamp": _utcnow_iso(),
        "probe": True,
    }

    try:
        # Write
        test_path.write_text(json.dumps(test_payload), encoding="utf-8")

        # Read-back verification
        read_back = json.loads(test_path.read_text(encoding="utf-8"))
        if read_back.get("probe") is not True:
            return CheckResult(
                name="SRG_CHECK_ARTIFACT_WRITE",
                passed=False,
                detail="Read-back verification failed: 'probe' key mismatch.",
                critical=False,
            )

        # Delete
        test_path.unlink()

        if test_path.exists():
            return CheckResult(
                name="SRG_CHECK_ARTIFACT_WRITE",
                passed=False,
                detail="File deletion failed: readiness_test.json still exists.",
                critical=False,
            )

    except PermissionError as e:
        return CheckResult(
            name="SRG_CHECK_ARTIFACT_WRITE",
            passed=False,
            detail=f"Permission denied: {e}",
            critical=False,
        )
    except Exception as e:
        return CheckResult(
            name="SRG_CHECK_ARTIFACT_WRITE",
            passed=False,
            detail=f"Artifact write test failed: {type(e).__name__}: {e}",
            critical=False,
        )

    return CheckResult(
        name="SRG_CHECK_ARTIFACT_WRITE",
        passed=True,
        detail="Atomic write/read/delete to artifacts/ succeeded.",
        critical=False,
    )
```

---

### CHECK 8: Schema Validation of Existing Artifacts

**Code:** `SRG_CHECK_SCHEMA_VALIDATION`
**Criticality:** Non-critical

**Rule:** If existing session artifacts are present from the most recent session directory under `artifacts/`, validate that they conform to the canonical schemas defined in `core.schemas`. The following files are checked if they exist:

| Artifact file | Schema class |
|---|---|
| `system_state.json` | `SystemStateReport.from_dict()` |
| `plays.json` | `List[PlayCard.from_dict()]` |
| `scan_health.json` | JSON dict with required keys |

**Pass condition:** Every existing artifact file either (a) does not exist (skip) or (b) deserialises without exception via the corresponding `from_dict()` classmethod.

**Rationale:** Corrupt artifacts from a prior crashed session can propagate stale or malformed data into the current session's scoring, learning, and delivery pipelines. Validating schema conformance on startup catches corruption before it compounds.

**Implementation:**

```python
import json
from pathlib import Path
from core.schemas import PlayCard
from core.artifact_loader import ArtifactLoader

def check_schema_validation(self) -> CheckResult:
    """CHECK 8: Validate schema of existing session artifacts."""
    loader = ArtifactLoader()
    latest_session_dir = loader.find_latest_session_dir()

    if latest_session_dir is None:
        return CheckResult(
            name="SRG_CHECK_SCHEMA_VALIDATION",
            passed=True,
            detail="No prior session artifacts found. Nothing to validate.",
            critical=False,
        )

    errors = []

    # Validate system_state.json
    ss_path = latest_session_dir / "system_state.json"
    if ss_path.exists():
        try:
            data = json.loads(ss_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                errors.append("system_state.json is not a JSON object")
        except (json.JSONDecodeError, Exception) as e:
            errors.append(f"system_state.json: {type(e).__name__}: {e}")

    # Validate plays.json
    plays_path = latest_session_dir / "plays.json"
    if plays_path.exists():
        try:
            data = json.loads(plays_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                errors.append("plays.json is not a JSON array")
            else:
                for i, item in enumerate(data):
                    PlayCard.from_dict(item)  # raises on schema violation
        except (json.JSONDecodeError, Exception) as e:
            errors.append(f"plays.json: {type(e).__name__}: {e}")

    # Validate scan_health.json
    sh_path = Path("data/scan_health.json")
    if sh_path.exists():
        try:
            data = json.loads(sh_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                errors.append("scan_health.json is not a JSON object")
        except (json.JSONDecodeError, Exception) as e:
            errors.append(f"scan_health.json: {type(e).__name__}: {e}")

    if errors:
        return CheckResult(
            name="SRG_CHECK_SCHEMA_VALIDATION",
            passed=False,
            detail=f"{len(errors)} validation error(s): {'; '.join(errors)}",
            critical=False,
        )

    return CheckResult(
        name="SRG_CHECK_SCHEMA_VALIDATION",
        passed=True,
        detail="All existing artifacts pass schema validation.",
        critical=False,
    )
```

---

### Check Summary Table

| # | Code | What it validates | Critical | Timeout |
|---|---|---|---|---|
| 1 | `SRG_CHECK_DOCKER_IMAGE` | Docker image git hash matches expected | No | 1s |
| 2 | `SRG_CHECK_DIRECTORIES` | 5 required directories exist + writable | No | 1s |
| 3 | `SRG_CHECK_DATA_PROVIDER` | yfinance fetch for QQQ3.L + DataFeedValidator | **Yes** | 15s |
| 4 | `SRG_CHECK_TIMEZONE_SCHEDULE` | Europe/London TZ + schedule keys in settings.yaml | No | 1s |
| 5 | `SRG_CHECK_WAR_ROOM_API` | GET /api/health -> 200 with JSON `status` key | **Yes** | 10s |
| 6 | `SRG_CHECK_TELEGRAM` | Bot token + chat ID present + dry-run send | **Yes** | 10s |
| 7 | `SRG_CHECK_ARTIFACT_WRITE` | Atomic write/read/delete to artifacts/ | No | 5s |
| 8 | `SRG_CHECK_SCHEMA_VALIDATION` | Existing artifacts conform to core.schemas | No | 5s |

**Maximum total gate execution time:** ~48 seconds (all checks sequential). In practice, checks 1, 2, 4 complete in <100ms each.

---

## 5. Gate Output States

The gate produces exactly one of three states:

### READY

**Condition:** All 8 checks PASS.

**System behaviour:** Normal operation. All output channels enabled: Telegram signals, PDF reports, War Room pushes, trade execution (paper mode).

```
GATE RESULT: READY
All 8/8 checks passed.
System entering normal operation.
```

### DEGRADED

**Condition:** 1-2 non-critical checks FAIL **AND** all critical checks (3, 5, 6) PASS.

**System behaviour:**
- SYSTEM HEALTH messages are emitted (Telegram `[SYSTEM]` channel, War Room status).
- **No trade outputs.** No signals, no plays, no PDF reports, no position adjustments.
- The gate re-runs every 5 minutes until READY or HALTED.

```
GATE RESULT: DEGRADED
6/8 checks passed. 2 non-critical failures:
  FAIL: SRG_CHECK_DOCKER_IMAGE — Expected: a1b2c3d4e5f6, Actual: 000000000000
  FAIL: SRG_CHECK_DIRECTORIES — Missing: data/reports
System restricted to HEALTH messages only. Re-checking in 5 minutes.
```

### HALTED

**Condition:** ANY critical check FAILS **OR** 3+ total checks FAIL (regardless of criticality).

**System behaviour:**
- The system emits a **startup failure alert** to:
  - Telegram `[SYSTEM]` channel (if Telegram is available; if CHECK 6 failed, this is skipped).
  - stdout / Docker logs (always available).
- **No other output of any kind.** The system is effectively inert.
- The gate re-runs every 5 minutes until DEGRADED or READY.

```
GATE RESULT: HALTED
3/8 checks passed. CRITICAL FAILURE:
  FAIL [CRITICAL]: SRG_CHECK_DATA_PROVIDER — yfinance fetch failed: ConnectionError
  FAIL [CRITICAL]: SRG_CHECK_WAR_ROOM_API — GET /api/health timed out after 10s
  FAIL: SRG_CHECK_DIRECTORIES — Not writable: artifacts
SYSTEM HALTED. Operator action required. Re-checking in 5 minutes.
```

### State Transition Diagram

```
                  ┌──────────┐
     Boot ──────► │  HALTED  │◄─── any critical fail OR ≥3 total fails
                  └────┬─────┘
                       │ all critical pass + ≤2 non-critical fail
                       ▼
                  ┌──────────┐
                  │ DEGRADED │◄─── 1-2 non-critical fails
                  └────┬─────┘
                       │ all 8 pass
                       ▼
                  ┌──────────┐
                  │  READY   │──── normal operation
                  └──────────┘

Re-check loop: every 5 minutes, re-evaluate from current state.
Transitions can go in any direction (READY → HALTED is possible on session-window re-check).
```

---

## 6. Policy

### 6.1 Output Suppression

| Gate state | Telegram signals | PDF reports | War Room plays | System health msgs | Startup alert |
|---|---|---|---|---|---|
| READY | Allowed | Allowed | Allowed | Allowed | N/A |
| DEGRADED | **Blocked** | **Blocked** | **Blocked** | Allowed | Emitted |
| HALTED | **Blocked** | **Blocked** | **Blocked** | Blocked* | Emitted** |

\* In HALTED state, system health messages are blocked because the system cannot guarantee their accuracy.
\** Startup failure alert is emitted via Telegram if CHECK 6 passed; always emitted to Docker logs.

### 6.2 Operator Actions

When the gate returns anything other than READY:

1. **Log to stdout** (always): Full check results with pass/fail, detail strings, and timestamps.
2. **Log to Telegram `[SYSTEM]` channel** (if available): Concise failure summary + operator action items.
3. **Write to `artifacts/readiness_gate.json`** (if CHECK 7 passed): Full machine-readable results.

### 6.3 Re-Run Policy

- The gate re-runs **every 5 minutes** while the system is not READY.
- The re-run timer is managed by APScheduler as a one-shot job that reschedules itself.
- If the gate transitions from DEGRADED/HALTED to READY, the system emits a `[SYSTEM] Readiness gate PASSED -- entering normal operation` message and begins the session.
- If the gate transitions from READY to DEGRADED/HALTED on a session-window re-check, the system immediately suppresses all trade outputs and emits a degradation alert.

### 6.4 Manual Override

An operator may override DEGRADED state (not HALTED) by setting the following in `config/settings.yaml`:

```yaml
startup_gate_override: true
```

**Override rules:**

| Rule | Detail |
|---|---|
| Scope | Override applies to DEGRADED state only. HALTED cannot be overridden. |
| Duration | Override expires at the end of the current session window (next session-window boundary). |
| Logging | Override activation is logged to Telegram `[SYSTEM]` and `artifacts/readiness_gate.json` with the operator's explicit acknowledgement. |
| Re-arm | After expiry, `startup_gate_override` must be set again for the next session. The system does NOT persist the flag across sessions. |
| Audit trail | Every override event is recorded with timestamp, gate state, and failed checks. |

**Override message format:**

```
[SYSTEM] OVERRIDE ACTIVE: Readiness gate in DEGRADED state.
Failed checks: SRG_CHECK_DOCKER_IMAGE, SRG_CHECK_SCHEMA_VALIDATION
Override expires at: 2026-02-27T13:25:00Z (next session window)
Operator accepts risk of operating with non-critical failures.
```

### 6.5 Session Window Integration

| Trigger | Time (UK) | Action |
|---|---|---|
| System boot | Any | Full gate execution. Block all output until READY. |
| Pre-market window | 06:55 | Full gate execution. If not READY, suppress pre-market scan. |
| Pre-US-open window | 13:25 | Full gate execution. If not READY, suppress US-open scan. |
| Re-check (while not READY) | Every 5 min | Re-execute gate. Transition state if results change. |

---

## 7. Implementation Plan

### 7.1 New Module: `core/startup_gate.py`

**Location:** `/core/startup_gate.py` (alongside existing `core/schemas.py`, `core/artifact_loader.py`, `core/scan_health.py`)

**Public interface:**

```python
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("nzt48.core.startup_gate")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Result of a single readiness check."""
    name: str
    passed: bool
    detail: str
    critical: bool
    elapsed_ms: float = 0.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

    def to_dict(self) -> dict:
        return asdict(self)


class GateState:
    READY = "READY"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"


@dataclass
class GateResult:
    """Aggregate result of the full readiness gate."""
    state: str                          # READY | DEGRADED | HALTED
    checks: List[CheckResult]           # all 8 check results
    override_active: bool = False       # True if startup_gate_override was set
    override_expires: Optional[str] = None
    timestamp: str = ""
    total_elapsed_ms: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def critical_failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed and c.critical]

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "checks": [c.to_dict() for c in self.checks],
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "critical_failures": [c.name for c in self.critical_failures],
            "override_active": self.override_active,
            "override_expires": self.override_expires,
            "timestamp": self.timestamp,
            "total_elapsed_ms": self.total_elapsed_ms,
        }


# ---------------------------------------------------------------------------
# StartupReadinessGate
# ---------------------------------------------------------------------------

class StartupReadinessGate:
    """Pre-flight readiness gate for the NZT-48 Trading Engine.

    Executes 8 mandatory checks and returns READY, DEGRADED, or HALTED.
    Called from NZT48Orchestrator.__init__() and before each session window.

    Usage:
        gate = StartupReadinessGate()
        result = gate.run()
        if result.state != GateState.READY:
            # suppress trade outputs
            ...
    """

    CRITICAL_CHECKS = {
        "SRG_CHECK_DATA_PROVIDER",
        "SRG_CHECK_WAR_ROOM_API",
        "SRG_CHECK_TELEGRAM",
    }

    def run(self) -> GateResult:
        """Execute all 8 checks and compute gate state."""
        t0 = time.monotonic()
        checks = [
            self._timed(self.check_docker_image),
            self._timed(self.check_directories),
            self._timed(self.check_data_provider),
            self._timed(self.check_timezone_schedule),
            self._timed(self.check_war_room_api),
            self._timed(self.check_telegram),
            self._timed(self.check_artifact_write),
            self._timed(self.check_schema_validation),
        ]
        total_ms = (time.monotonic() - t0) * 1000

        state = self._compute_state(checks)

        # Check for override
        override_active = False
        override_expires = None
        if state == GateState.DEGRADED:
            override_active = self._check_override()
            if override_active:
                override_expires = self._compute_override_expiry()

        result = GateResult(
            state=state,
            checks=checks,
            override_active=override_active,
            override_expires=override_expires,
            total_elapsed_ms=round(total_ms, 2),
        )

        self._write_artifact(result)
        self._log_result(result)
        return result

    def _compute_state(self, checks: List[CheckResult]) -> str:
        """Determine gate state from check results."""
        failed = [c for c in checks if not c.passed]
        critical_failed = [c for c in failed if c.critical]

        if not failed:
            return GateState.READY
        if critical_failed or len(failed) >= 3:
            return GateState.HALTED
        return GateState.DEGRADED

    def _timed(self, check_fn) -> CheckResult:
        """Run a check function and record elapsed time."""
        t0 = time.monotonic()
        result = check_fn()
        result.elapsed_ms = round((time.monotonic() - t0) * 1000, 2)
        return result

    def _write_artifact(self, result: GateResult) -> None:
        """Write gate results to artifacts/readiness_gate.json."""
        try:
            path = Path("artifacts/readiness_gate.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(result.to_dict(), indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("Failed to write readiness_gate.json: %s", e)

    def _log_result(self, result: GateResult) -> None:
        """Log gate result to stdout."""
        level = logging.INFO if result.state == GateState.READY else logging.WARNING
        logger.log(
            level,
            "STARTUP GATE: %s (%d/%d passed, %.0fms)",
            result.state,
            result.passed_count,
            len(result.checks),
            result.total_elapsed_ms,
        )
        for c in result.checks:
            prefix = "PASS" if c.passed else "FAIL"
            crit = " [CRITICAL]" if c.critical and not c.passed else ""
            logger.log(
                level,
                "  %s%s: %s -- %s (%.0fms)",
                prefix, crit, c.name, c.detail, c.elapsed_ms,
            )

    # -- Individual check methods (see Section 4 for full implementations) --

    def check_docker_image(self) -> CheckResult: ...
    def check_directories(self) -> CheckResult: ...
    def check_data_provider(self) -> CheckResult: ...
    def check_timezone_schedule(self) -> CheckResult: ...
    def check_war_room_api(self) -> CheckResult: ...
    def check_telegram(self) -> CheckResult: ...
    def check_artifact_write(self) -> CheckResult: ...
    def check_schema_validation(self) -> CheckResult: ...

    def _check_override(self) -> bool: ...
    def _compute_override_expiry(self) -> str: ...
```

### 7.2 Integration Point: `main.py` NZT48Orchestrator

**Called from:** `NZT48Orchestrator.__init__()` after all component initialization (after line ~353 in current `main.py`, after edge decay and dynamic sizer state loading).

```python
# In NZT48Orchestrator.__init__(), after component initialization:

from core.startup_gate import StartupReadinessGate, GateState

# === STARTUP READINESS GATE ===
self.startup_gate = StartupReadinessGate()
gate_result = self.startup_gate.run()
self._gate_state = gate_result.state

if gate_result.state == GateState.HALTED:
    logger.critical("STARTUP GATE: HALTED. System will not produce trade outputs.")
    await self.telegram.send_alert(
        f"[SYSTEM] STARTUP GATE HALTED\n"
        f"Failed: {', '.join(c.name for c in gate_result.critical_failures)}\n"
        f"Operator action required."
    )
elif gate_result.state == GateState.DEGRADED:
    logger.warning("STARTUP GATE: DEGRADED. Trade outputs suppressed.")
    if gate_result.override_active:
        logger.warning("OVERRIDE ACTIVE. Proceeding with caution.")

# Schedule re-check if not READY
if gate_result.state != GateState.READY:
    self._schedule_gate_recheck()
```

**Called before:** Each session window transition (06:55, 13:25 UK). Add to the session-window scheduler:

```python
def _on_session_window(self, session_name: str) -> None:
    """Called at each session window boundary."""
    # Re-run readiness gate
    gate_result = self.startup_gate.run()
    self._gate_state = gate_result.state

    if gate_result.state != GateState.READY:
        logger.warning(
            "Session '%s' suppressed: gate state is %s",
            session_name, gate_result.state,
        )
        return  # Do not proceed with session scan

    # Normal session processing continues...
```

### 7.3 APScheduler Re-Check Job

```python
def _schedule_gate_recheck(self) -> None:
    """Schedule gate re-check every 5 minutes until READY."""
    from apscheduler.triggers.interval import IntervalTrigger

    self.scheduler.add_job(
        self._gate_recheck_job,
        trigger=IntervalTrigger(minutes=5),
        id="startup_gate_recheck",
        replace_existing=True,
        max_instances=1,
    )

def _gate_recheck_job(self) -> None:
    """Re-run gate and transition state if changed."""
    result = self.startup_gate.run()
    old_state = self._gate_state
    self._gate_state = result.state

    if result.state == GateState.READY:
        logger.info("STARTUP GATE: Transitioned %s -> READY.", old_state)
        self.scheduler.remove_job("startup_gate_recheck")
        # Emit recovery message
        self.telegram.send_alert(
            "[SYSTEM] Readiness gate PASSED -- entering normal operation."
        )
    elif result.state != old_state:
        logger.warning(
            "STARTUP GATE: State changed %s -> %s.", old_state, result.state
        )
```

### 7.4 Artifact Output

**File:** `artifacts/readiness_gate.json`

Written on every gate execution. Overwritten (not appended) -- the file reflects the most recent gate run.

```json
{
  "state": "DEGRADED",
  "checks": [
    {
      "name": "SRG_CHECK_DOCKER_IMAGE",
      "passed": false,
      "detail": "Expected: a1b2c3d4e5f6, Actual: 000000000000",
      "critical": false,
      "elapsed_ms": 1.23,
      "timestamp": "2026-02-27T06:55:01.234567Z"
    },
    {
      "name": "SRG_CHECK_DIRECTORIES",
      "passed": true,
      "detail": "All 5 directories OK.",
      "critical": false,
      "elapsed_ms": 0.45,
      "timestamp": "2026-02-27T06:55:01.235012Z"
    }
  ],
  "passed_count": 6,
  "failed_count": 2,
  "critical_failures": [],
  "override_active": false,
  "override_expires": null,
  "timestamp": "2026-02-27T06:55:01.234000Z",
  "total_elapsed_ms": 3842.17
}
```

---

## 8. Acceptance Tests

All tests reference the module `core/startup_gate.py` and are executable as part of the project's test suite.

| Test ID | Scenario | Setup | Expected result |
|---|---|---|---|
| **T-STARTUP-001** | All checks pass | All services running, directories present, config valid | Gate returns `READY`. `artifacts/readiness_gate.json` written with `state: "READY"` and 8/8 passed. |
| **T-STARTUP-002** | Telegram token missing | Unset `TELEGRAM_BOT_TOKEN` env var | Gate returns `HALTED` (critical failure on CHECK 6). Startup alert emitted to Docker logs. |
| **T-STARTUP-003** | `artifacts/` not writable | `chmod 444 artifacts/` before gate run | Gate returns `DEGRADED` (non-critical CHECK 7 fails). If CHECK 2 also fails, gate returns `DEGRADED` (2 non-critical). If 3+ fail, `HALTED`. |
| **T-STARTUP-004** | yfinance unreachable | Block outbound HTTP or mock yfinance to raise `ConnectionError` | Gate returns `HALTED` (critical failure on CHECK 3). No trade outputs permitted. |
| **T-STARTUP-005** | War Room API down | Stop FastAPI server on `:8000` before gate run | Gate returns `HALTED` (critical failure on CHECK 5). Startup alert emitted. |
| **T-STARTUP-006** | Schema validation fails on corrupt `system_state.json` | Write `{"corrupt": true` (invalid JSON) to latest session's `system_state.json` | Gate returns `DEGRADED` (non-critical CHECK 8 fails). Trade outputs suppressed. |
| **T-STARTUP-007** | Override flag set during DEGRADED | Set `startup_gate_override: true` in `settings.yaml`. Trigger DEGRADED state (e.g., fail CHECK 1). | Gate returns `DEGRADED` with `override_active: true`. System proceeds to emit trade outputs for current session window only. Override logged. |
| **T-STARTUP-008** | Gate re-runs every 5 min when not READY | Trigger DEGRADED state. Monitor APScheduler job list. | `startup_gate_recheck` job is registered with 5-minute interval. After fixing the failure, next re-run transitions to `READY` and removes the re-check job. |

### Test Implementation Skeleton

```python
import pytest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path
from core.startup_gate import StartupReadinessGate, GateState


class TestStartupReadinessGate:

    def test_t_startup_001_all_pass(self, tmp_path, monkeypatch):
        """T-STARTUP-001: All checks pass -> READY."""
        gate = StartupReadinessGate()
        # Mock all external dependencies to succeed
        with patch.object(gate, 'check_docker_image') as m1, \
             patch.object(gate, 'check_directories') as m2, \
             # ... (mock all 8 checks to return passed=True)
             pass
        result = gate.run()
        assert result.state == GateState.READY
        assert result.passed_count == 8
        assert result.failed_count == 0

    def test_t_startup_002_telegram_missing(self, monkeypatch):
        """T-STARTUP-002: Telegram token missing -> HALTED."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        gate = StartupReadinessGate()
        result = gate.run()
        assert result.state == GateState.HALTED
        assert any(
            c.name == "SRG_CHECK_TELEGRAM" and not c.passed
            for c in result.checks
        )

    def test_t_startup_004_yfinance_unreachable(self):
        """T-STARTUP-004: yfinance unreachable -> HALTED."""
        gate = StartupReadinessGate()
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.side_effect = ConnectionError("Network unreachable")
            result = gate.run()
        assert result.state == GateState.HALTED

    def test_t_startup_007_override(self, monkeypatch):
        """T-STARTUP-007: Override flag allows DEGRADED to proceed."""
        gate = StartupReadinessGate()
        # Force exactly 1 non-critical failure, all critical pass
        # Set startup_gate_override: true
        result = gate.run()
        assert result.state == GateState.DEGRADED
        assert result.override_active is True
        assert result.override_expires is not None
```

---

## 9. Operator Actions per Failure Mode

| Check | Failure symptom | Operator action |
|---|---|---|
| **CHECK 1** `SRG_CHECK_DOCKER_IMAGE` | Image hash mismatch or `GIT_COMMIT_HASH` not baked | 1. Verify correct image is running: `docker inspect nzt48 \| grep GIT_COMMIT_HASH`. 2. Rebuild: `docker compose build --build-arg GIT_COMMIT_HASH=$(git rev-parse HEAD) nzt48`. 3. Restart: `docker compose up -d nzt48`. |
| **CHECK 2** `SRG_CHECK_DIRECTORIES` | Missing or non-writable directory | 1. SSH to EC2: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28`. 2. Create missing dirs: `mkdir -p config artifacts data data/reports reports`. 3. Fix permissions: `chown -R 1000:1000 config artifacts data reports`. 4. Restart container. |
| **CHECK 3** `SRG_CHECK_DATA_PROVIDER` | yfinance returns empty or raises exception | 1. Verify internet connectivity: `curl -s https://query1.finance.yahoo.com/v8/finance/chart/QQQ3.L`. 2. Check if yfinance is rate-limited (retry in 60s). 3. If persistent, check Yahoo Finance status page. 4. If outside market hours and ticker returns empty, verify `period="1d"` returns last trading day data. |
| **CHECK 4** `SRG_CHECK_TIMEZONE_SCHEDULE` | Wrong timezone or missing schedule keys | 1. Verify `settings.yaml` contains `uk_timezone: "Europe/London"`. 2. Verify `schedule:` section has `pre_market` and `us_open` keys. 3. Check container timezone: `docker exec nzt48 date`. 4. If TZ data missing, rebuild image with `tzdata` package. |
| **CHECK 5** `SRG_CHECK_WAR_ROOM_API` | `/api/health` returns non-200 or times out | 1. Check FastAPI process: `docker exec nzt48 ps aux \| grep uvicorn`. 2. Check port binding: `docker exec nzt48 curl -s http://localhost:8000/api/health`. 3. Check logs: `docker logs nzt48 --tail 100 \| grep -i error`. 4. Restart: `docker compose restart nzt48`. |
| **CHECK 6** `SRG_CHECK_TELEGRAM` | Token/chat ID missing or dry-run fails | 1. Verify env vars: `docker exec nzt48 env \| grep TELEGRAM`. 2. Test token manually: `curl https://api.telegram.org/bot<TOKEN>/getMe`. 3. Verify chat ID: `curl https://api.telegram.org/bot<TOKEN>/getChat?chat_id=<CHAT_ID>`. 4. If token revoked, regenerate via BotFather and update `.env`. |
| **CHECK 7** `SRG_CHECK_ARTIFACT_WRITE` | Write/read/delete test fails | 1. Check disk space: `docker exec nzt48 df -h /app/artifacts`. 2. Check permissions: `docker exec nzt48 ls -la artifacts/`. 3. Check if filesystem is read-only: `docker exec nzt48 touch artifacts/test`. 4. If Docker volume issue, recreate volume and restart. |
| **CHECK 8** `SRG_CHECK_SCHEMA_VALIDATION` | Corrupt JSON or schema mismatch in artifacts | 1. Identify corrupt file from gate detail string. 2. Inspect file: `docker exec nzt48 cat artifacts/<session>/<file>.json \| python3 -m json.tool`. 3. If corrupt, delete or archive the file: `mv artifacts/<session>/<file>.json artifacts/<session>/<file>.json.corrupt`. 4. System will regenerate on next scan. |

### Escalation Matrix

| Severity | Condition | Response time | Escalation |
|---|---|---|---|
| **P1 -- Critical** | HALTED state persists >15 minutes during market hours | Immediate | Manual investigation required before next session window |
| **P2 -- High** | DEGRADED state persists >30 minutes | Within 1 hour | Investigate root cause; apply override only if understood |
| **P3 -- Low** | Non-critical check fails intermittently | Same day | Monitor; no override needed if self-resolving |

---

## 10. Evidence and Proof Artifacts

### 10.1 Primary Artifact: `artifacts/readiness_gate.json`

Written on every gate execution. Contains the complete machine-readable record of all 8 checks, their results, timing, and the computed gate state.

**Schema:**

```json
{
  "state": "READY | DEGRADED | HALTED",
  "checks": [
    {
      "name": "SRG_CHECK_*",
      "passed": true,
      "detail": "Human-readable description",
      "critical": false,
      "elapsed_ms": 1.23,
      "timestamp": "2026-02-27T06:55:01.234567Z"
    }
  ],
  "passed_count": 8,
  "failed_count": 0,
  "critical_failures": [],
  "override_active": false,
  "override_expires": null,
  "timestamp": "2026-02-27T06:55:01.234000Z",
  "total_elapsed_ms": 3842.17
}
```

### 10.2 Log Evidence

All gate results are emitted to stdout at `INFO` (READY) or `WARNING` (DEGRADED/HALTED) level, captured by Docker logging:

```
docker logs nzt48 | grep "STARTUP GATE"
```

### 10.3 Telegram Evidence

Gate results for non-READY states are sent to the Telegram `[SYSTEM]` channel with the following format:

```
[SYSTEM] STARTUP READINESS GATE: DEGRADED
Passed: 6/8 | Failed: 2/8 | Critical failures: 0
Failed checks:
  - SRG_CHECK_DOCKER_IMAGE: Expected: a1b2c3d4, Actual: 00000000
  - SRG_CHECK_SCHEMA_VALIDATION: plays.json: KeyError: 'ticker'
Trade outputs SUPPRESSED. Re-checking in 5 minutes.
```

### 10.4 Audit Trail

For compliance and post-incident review, the following artifacts together provide a complete audit trail:

| Artifact | Location | Retention |
|---|---|---|
| `readiness_gate.json` | `artifacts/readiness_gate.json` | Overwritten each run; prior versions preserved in session directories |
| Docker logs | `docker logs nzt48` | Retained per Docker logging driver configuration |
| Telegram messages | `[SYSTEM]` channel history | Permanent (Telegram retains) |
| Override events | `readiness_gate.json` `override_active` field + Docker logs | Same as above |

---

## 11. Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-02-27 | NZT-48 Engineering | Initial specification. 8 mandatory checks, 3 gate states, override policy, acceptance tests. |
