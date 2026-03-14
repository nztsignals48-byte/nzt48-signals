# NZT-48 Continuous Integrity Monitor Specification

**Document ID:** NZT48-ANNEX-CIM-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** BINDING
**Owner:** NZT-48 Trading Engine
**Classification:** Internal / Operational

---

## Table of Contents

1. [Objective](#1-objective)
2. [Integrity Checks](#2-integrity-checks)
3. [Wiring Drift Signatures](#3-wiring-drift-signatures)
4. [Detection Policy](#4-detection-policy)
5. [Implementation](#5-implementation)
6. [Operator Playbooks](#6-operator-playbooks)
7. [Acceptance Tests](#7-acceptance-tests)
8. [Proof Artifacts](#8-proof-artifacts)
9. [Revision History](#9-revision-history)

---

## 1. Objective

Define a lightweight monitor that runs every 5 minutes (market hours) or every 15 minutes (off-hours) to detect **wiring drift** -- situations where system components have silently diverged from each other, producing inconsistent or stale output even though individual components appear healthy in isolation.

**The Problem This Solves:** A system can pass all individual health checks -- engine running, API responding, Telegram connected -- yet still produce corrupt output because the data pipeline between components has broken. The engine may run but not write artifacts. Artifacts may update but the War Room may not read them. The War Room may display data but Telegram may not gate on it. These are "wiring" failures: the components are alive but disconnected.

**Design Principle:** The integrity monitor checks the *connections between components*, not the components themselves. Component-level health is the domain of readiness checks and sanity gates. Wiring integrity is the domain of this monitor.

**Scope:** All data pipelines within the NZT-48 system boundary (engine -> artifacts -> War Room -> Telegram -> PDF). External provider health is monitored by the Provenance Spec and is NOT in scope for this monitor.

---

## 2. Integrity Checks

All checks run on a fixed schedule. During market hours (08:00-16:30 UK for LSE, 09:30-16:00 ET for US), checks run every 5 minutes. Outside market hours, checks run every 15 minutes.

### 2.1 Scan SLA

**Check ID:** `INTEG-SCAN`

**Purpose:** Verify the core scan loop is executing within its SLA.

| Metric | Threshold | Source | Failure Code |
|---|---|---|---|
| Last tick time | Within 120 seconds of now | `artifacts/system_state.json` field `last_tick_time` | `INTEG_SCAN_TICK_STALE` |
| Last engine run | Within 180 seconds of now | `artifacts/system_state.json` field `last_engine_run` | `INTEG_SCAN_ENGINE_STALE` |

**Logic:**

```python
def check_scan_sla() -> IntegrityResult:
    state = load_json("artifacts/system_state.json")
    now = datetime.now(timezone.utc)
    results = []

    # Last tick freshness
    last_tick = parse_iso(state.get("last_tick_time"))
    tick_age = (now - last_tick).total_seconds() if last_tick else float("inf")
    if tick_age > 120:
        results.append(IntegrityFailure(
            code="INTEG_SCAN_TICK_STALE",
            detail=f"Last tick {tick_age:.0f}s ago (threshold: 120s)",
            measured_value=tick_age,
            threshold=120
        ))

    # Last engine run freshness
    last_run = parse_iso(state.get("last_engine_run"))
    run_age = (now - last_run).total_seconds() if last_run else float("inf")
    if run_age > 180:
        results.append(IntegrityFailure(
            code="INTEG_SCAN_ENGINE_STALE",
            detail=f"Last engine run {run_age:.0f}s ago (threshold: 180s)",
            measured_value=run_age,
            threshold=180
        ))

    return IntegrityResult(check_id="INTEG-SCAN", failures=results)
```

---

### 2.2 Artifact Freshness

**Check ID:** `INTEG-ART`

**Purpose:** Verify all critical artifacts are being written within their expected cadence.

| Artifact | File | Expected TTL (Market Hours) | Expected TTL (Off-Hours) | Failure Code |
|---|---|---|---|---|
| System state | `artifacts/system_state.json` | 180 seconds | 900 seconds | `INTEG_ART_SYSTEM_STATE_STALE` |
| Active plays | `artifacts/plays.json` | 300 seconds | 900 seconds | `INTEG_ART_PLAYS_STALE` |
| Scan health | `artifacts/scan_health.json` | 180 seconds | 900 seconds | `INTEG_ART_SCAN_HEALTH_STALE` |
| Readiness status | `artifacts/readiness_status.json` | 600 seconds | 600 seconds | `INTEG_ART_READINESS_STALE` |
| Integrity status | `artifacts/integrity_status.json` | 600 seconds | 1800 seconds | `INTEG_ART_INTEGRITY_STALE` |

**Logic:**

```python
ARTIFACT_TTLS = {
    "artifacts/system_state.json":    {"market": 180,  "off": 900},
    "artifacts/plays.json":           {"market": 300,  "off": 900},
    "artifacts/scan_health.json":     {"market": 180,  "off": 900},
    "artifacts/readiness_status.json": {"market": 600,  "off": 600},
    "artifacts/integrity_status.json": {"market": 600,  "off": 1800},
}

def check_artifact_freshness() -> IntegrityResult:
    now = datetime.now(timezone.utc)
    is_market = is_market_hours(now)
    results = []

    for path, ttls in ARTIFACT_TTLS.items():
        ttl = ttls["market"] if is_market else ttls["off"]
        artifact = load_json(path)

        if artifact is None:
            results.append(IntegrityFailure(
                code=f"INTEG_ART_{artifact_key(path)}_MISSING",
                detail=f"Artifact {path} does not exist or is unreadable",
                measured_value=None,
                threshold=ttl
            ))
            continue

        as_of = parse_iso(artifact.get("as_of") or artifact.get("timestamp"))
        if as_of is None:
            results.append(IntegrityFailure(
                code=f"INTEG_ART_{artifact_key(path)}_NO_TIMESTAMP",
                detail=f"Artifact {path} has no as_of or timestamp field",
                measured_value=None,
                threshold=ttl
            ))
            continue

        age = (now - as_of).total_seconds()
        if age > ttl:
            results.append(IntegrityFailure(
                code=f"INTEG_ART_{artifact_key(path)}_STALE",
                detail=f"Artifact {path} is {age:.0f}s old (threshold: {ttl}s)",
                measured_value=age,
                threshold=ttl
            ))

    return IntegrityResult(check_id="INTEG-ART", failures=results)
```

---

### 2.3 War Room State Freshness

**Check ID:** `INTEG-WR`

**Purpose:** Verify the War Room API is serving current data, not stale cached state.

| Metric | Threshold | Source | Failure Code |
|---|---|---|---|
| API health `last_update` | Within 300 seconds of now | `GET /api/health` response field `last_update` | `INTEG_WR_STALE` |
| API health response time | Under 5000 milliseconds | `GET /api/health` response time | `INTEG_WR_SLOW` |
| API health HTTP status | 200 | `GET /api/health` HTTP status code | `INTEG_WR_UNHEALTHY` |

**Logic:**

```python
def check_war_room_freshness() -> IntegrityResult:
    now = datetime.now(timezone.utc)
    results = []

    try:
        start = time.monotonic()
        resp = requests.get("http://localhost:3001/api/health", timeout=5)
        elapsed_ms = (time.monotonic() - start) * 1000

        if resp.status_code != 200:
            results.append(IntegrityFailure(
                code="INTEG_WR_UNHEALTHY",
                detail=f"War Room returned HTTP {resp.status_code}",
                measured_value=resp.status_code,
                threshold=200
            ))
            return IntegrityResult(check_id="INTEG-WR", failures=results)

        if elapsed_ms > 5000:
            results.append(IntegrityFailure(
                code="INTEG_WR_SLOW",
                detail=f"War Room responded in {elapsed_ms:.0f}ms (threshold: 5000ms)",
                measured_value=elapsed_ms,
                threshold=5000
            ))

        data = resp.json()
        last_update = parse_iso(data.get("last_update"))
        if last_update:
            age = (now - last_update).total_seconds()
            if age > 300:
                results.append(IntegrityFailure(
                    code="INTEG_WR_STALE",
                    detail=f"War Room last_update {age:.0f}s ago (threshold: 300s)",
                    measured_value=age,
                    threshold=300
                ))

    except requests.RequestException as e:
        results.append(IntegrityFailure(
            code="INTEG_WR_UNREACHABLE",
            detail=f"War Room health endpoint unreachable: {e}",
            measured_value=None,
            threshold=None
        ))

    return IntegrityResult(check_id="INTEG-WR", failures=results)
```

---

### 2.4 Telegram Suppression Counts

**Check ID:** `INTEG-TG`

**Purpose:** Detect excessive signal suppression that may indicate a silent gating failure.

| Metric | Threshold | Source | Failure Code |
|---|---|---|---|
| Invalid-score suppression count (rolling 60 min) | > 5 suppressions | In-memory suppression counter | `INTEG_TG_HIGH_SUPPRESSION` |
| Consecutive suppression streak | > 10 consecutive signals suppressed | In-memory counter | `INTEG_TG_SUPPRESSION_STREAK` |
| Zero messages sent (market hours, rolling 60 min) | 0 messages when engine is active | Telegram send log | `INTEG_TG_SILENT` |

**Logic:**

```python
def check_telegram_suppression() -> IntegrityResult:
    results = []
    counters = get_telegram_counters()
    now = datetime.now(timezone.utc)

    # Invalid-score suppression rate
    invalid_score_60m = counters.get_suppression_count(
        reason="invalid_score",
        window=timedelta(minutes=60)
    )
    if invalid_score_60m > 5:
        results.append(IntegrityFailure(
            code="INTEG_TG_HIGH_SUPPRESSION",
            detail=f"{invalid_score_60m} invalid-score suppressions in 60min (threshold: 5)",
            measured_value=invalid_score_60m,
            threshold=5
        ))

    # Consecutive suppression streak
    streak = counters.get_consecutive_suppression_streak()
    if streak > 10:
        results.append(IntegrityFailure(
            code="INTEG_TG_SUPPRESSION_STREAK",
            detail=f"{streak} consecutive suppressions (threshold: 10)",
            measured_value=streak,
            threshold=10
        ))

    # Silent during market hours
    if is_market_hours(now):
        sent_60m = counters.get_messages_sent(window=timedelta(minutes=60))
        engine_active = counters.is_engine_active()
        if sent_60m == 0 and engine_active:
            results.append(IntegrityFailure(
                code="INTEG_TG_SILENT",
                detail="Zero Telegram messages sent in 60min during active market hours",
                measured_value=0,
                threshold=1
            ))

    return IntegrityResult(check_id="INTEG-TG", failures=results)
```

---

### 2.5 PDF Audit Pass Rate

**Check ID:** `INTEG-PDF`

**Purpose:** Verify the most recent PDF generation passed its quality audit.

| Metric | Threshold | Source | Failure Code |
|---|---|---|---|
| Last PDF QA result | Must be `PASS` | `artifacts/pdf_audit.json` field `last_result` | `INTEG_PDF_QA_FAIL` |
| Last PDF generation age | Within expected schedule + 30 min tolerance | `artifacts/pdf_audit.json` field `last_generated_at` | `INTEG_PDF_OVERDUE` |

**Logic:**

```python
def check_pdf_audit() -> IntegrityResult:
    results = []
    audit = load_json("artifacts/pdf_audit.json")

    if audit is None:
        results.append(IntegrityFailure(
            code="INTEG_PDF_NO_AUDIT",
            detail="No PDF audit artifact found",
            measured_value=None,
            threshold=None
        ))
        return IntegrityResult(check_id="INTEG-PDF", failures=results)

    # QA result check
    last_result = audit.get("last_result")
    if last_result != "PASS":
        results.append(IntegrityFailure(
            code="INTEG_PDF_QA_FAIL",
            detail=f"Last PDF QA result: {last_result} (expected: PASS)",
            measured_value=last_result,
            threshold="PASS"
        ))

    # PDF schedule adherence
    last_gen = parse_iso(audit.get("last_generated_at"))
    if last_gen:
        next_expected = get_next_expected_pdf_time(last_gen)
        now = datetime.now(timezone.utc)
        if now > next_expected + timedelta(minutes=30):
            overdue_min = (now - next_expected).total_seconds() / 60
            results.append(IntegrityFailure(
                code="INTEG_PDF_OVERDUE",
                detail=f"PDF overdue by {overdue_min:.0f} minutes",
                measured_value=overdue_min,
                threshold=30
            ))

    return IntegrityResult(check_id="INTEG-PDF", failures=results)
```

---

### 2.6 Provider Health

**Check ID:** `INTEG-PROV`

**Purpose:** Verify that a sufficient proportion of data providers are reporting healthy status.

| Metric | Threshold | Source | Failure Code |
|---|---|---|---|
| Provider OK rate | > 80% of active providers returning OK | Provider health registry | `INTEG_PROV_DEGRADED` |
| Provider total failure | 0% of providers returning OK | Provider health registry | `INTEG_PROV_ALL_DOWN` |

**Logic:**

```python
def check_provider_health() -> IntegrityResult:
    results = []
    providers = get_provider_health_registry()

    total = len(providers)
    if total == 0:
        results.append(IntegrityFailure(
            code="INTEG_PROV_NO_REGISTRY",
            detail="Provider health registry is empty",
            measured_value=0,
            threshold=1
        ))
        return IntegrityResult(check_id="INTEG-PROV", failures=results)

    ok_count = sum(1 for p in providers.values() if p.status == "OK")
    ok_rate = ok_count / total

    if ok_rate == 0:
        results.append(IntegrityFailure(
            code="INTEG_PROV_ALL_DOWN",
            detail=f"All {total} providers reporting failure",
            measured_value=0.0,
            threshold=0.80
        ))
    elif ok_rate <= 0.80:
        failed = [name for name, p in providers.items() if p.status != "OK"]
        results.append(IntegrityFailure(
            code="INTEG_PROV_DEGRADED",
            detail=f"Provider OK rate: {ok_rate:.0%} ({ok_count}/{total}). "
                   f"Failed: {', '.join(failed)}",
            measured_value=ok_rate,
            threshold=0.80
        ))

    return IntegrityResult(check_id="INTEG-PROV", failures=results)
```

---

### 2.7 Provider Price Disagreement

**Check ID:** `INTEG-DISAGREE`

**Purpose:** Detect situations where multiple providers report materially different prices for the same ticker, indicating a stale feed or data corruption in one provider.

| Metric | Threshold | Source | Failure Code |
|---|---|---|---|
| Cross-provider price divergence | > 2% for same ticker at same timestamp | Provenance cache (multi-provider entries) | `INTEG_DISAGREE_PRICE` |

**Logic:**

```python
def check_provider_disagreement() -> IntegrityResult:
    results = []
    cache = get_provenance_cache()

    # Group price records by ticker
    by_ticker = group_by_ticker(cache, field="close")

    for ticker, records in by_ticker.items():
        if len(records) < 2:
            continue  # Need at least 2 providers to compare

        # Only compare records within 120s of each other
        recent = [r for r in records if r.age_seconds < 120]
        if len(recent) < 2:
            continue

        prices = [(r.provider, r.value) for r in recent]
        min_price = min(p[1] for p in prices)
        max_price = max(p[1] for p in prices)

        if min_price <= 0:
            continue  # Avoid division by zero

        divergence = (max_price - min_price) / min_price
        if divergence > 0.02:
            min_provider = next(p[0] for p in prices if p[1] == min_price)
            max_provider = next(p[0] for p in prices if p[1] == max_price)
            results.append(IntegrityFailure(
                code="INTEG_DISAGREE_PRICE",
                detail=f"{ticker}: {min_provider}={min_price:.4f} vs "
                       f"{max_provider}={max_price:.4f} "
                       f"(divergence: {divergence:.2%}, threshold: 2%)",
                measured_value=divergence,
                threshold=0.02
            ))

    return IntegrityResult(check_id="INTEG-DISAGREE", failures=results)
```

---

### Check Summary Table

| Check ID | What It Detects | Frequency (Market) | Frequency (Off-Hours) | Critical Level |
|---|---|---|---|---|
| `INTEG-SCAN` | Engine scan loop stalled | 5 min | 15 min | HIGH |
| `INTEG-ART` | Artifact pipeline broken | 5 min | 15 min | HIGH |
| `INTEG-WR` | War Room serving stale data | 5 min | 15 min | MEDIUM |
| `INTEG-TG` | Telegram silently suppressing everything | 5 min | 15 min | HIGH |
| `INTEG-PDF` | PDF generation failing QA | 5 min | 15 min | MEDIUM |
| `INTEG-PROV` | Data providers degraded | 5 min | 15 min | HIGH |
| `INTEG-DISAGREE` | Provider data corruption/staleness | 5 min | 15 min | MEDIUM |

---

## 3. Wiring Drift Signatures

Wiring drift signatures are specific failure patterns where the integrity monitor detects that data is flowing through some pipeline stages but not others. Each signature maps to a specific broken link in the data pipeline.

### Pipeline Architecture

```
[Engine/Scan Loop] --> [Artifacts] --> [War Room API] --> [Telegram Gating] --> [PDF Generation]
         |                  |                |                   |                    |
    INTEG-SCAN         INTEG-ART        INTEG-WR           INTEG-TG            INTEG-PDF
```

### 3.1 DRIFT_ENGINE_ARTIFACT

**Signature:** Engine scan loop is running (last tick within SLA) but artifacts are NOT being written (artifact timestamps stale).

**Detection Logic:**

```python
def detect_drift_engine_artifact(
    scan_result: IntegrityResult,
    art_result: IntegrityResult
) -> Optional[DriftSignature]:
    scan_ok = len(scan_result.failures) == 0
    art_ok = len(art_result.failures) == 0

    if scan_ok and not art_ok:
        stale_artifacts = [f.code for f in art_result.failures]
        return DriftSignature(
            code="DRIFT_ENGINE_ARTIFACT",
            description="Engine is running but artifacts are not being written",
            upstream_status="HEALTHY (scan loop active)",
            downstream_status=f"STALE ({', '.join(stale_artifacts)})",
            probable_causes=[
                "Artifact write path has an unhandled exception",
                "Disk full or permission error on artifacts/ directory",
                "JSON serialisation failure on system_state or plays",
                "APScheduler job for artifact writing has stopped"
            ],
            severity="HIGH"
        )
    return None
```

---

### 3.2 DRIFT_ARTIFACT_WARROOM

**Signature:** Artifacts are being written (fresh timestamps) but the War Room API is serving stale data.

**Detection Logic:**

```python
def detect_drift_artifact_warroom(
    art_result: IntegrityResult,
    wr_result: IntegrityResult
) -> Optional[DriftSignature]:
    art_ok = len(art_result.failures) == 0
    wr_stale = any(f.code == "INTEG_WR_STALE" for f in wr_result.failures)

    if art_ok and wr_stale:
        return DriftSignature(
            code="DRIFT_ARTIFACT_WARROOM",
            description="Artifacts are fresh but War Room is not reading them",
            upstream_status="HEALTHY (artifacts fresh)",
            downstream_status="STALE (War Room last_update behind)",
            probable_causes=[
                "War Room file watcher has stopped",
                "War Room is reading from wrong artifacts path",
                "War Room API process hung (alive but not processing)",
                "War Room in-memory cache not invalidating on file change"
            ],
            severity="HIGH"
        )
    return None
```

---

### 3.3 DRIFT_WARROOM_TELEGRAM

**Signature:** War Room is updating (fresh data, healthy API) but Telegram output is not being properly gated -- signals are either all being suppressed or all being sent without proper checks.

**Detection Logic:**

```python
def detect_drift_warroom_telegram(
    wr_result: IntegrityResult,
    tg_result: IntegrityResult
) -> Optional[DriftSignature]:
    wr_ok = len(wr_result.failures) == 0
    tg_suppressed = any(
        f.code in ("INTEG_TG_HIGH_SUPPRESSION", "INTEG_TG_SUPPRESSION_STREAK", "INTEG_TG_SILENT")
        for f in tg_result.failures
    )

    if wr_ok and tg_suppressed:
        return DriftSignature(
            code="DRIFT_WARROOM_TELEGRAM",
            description="War Room is healthy but Telegram is excessively suppressed or silent",
            upstream_status="HEALTHY (War Room fresh)",
            downstream_status="SUPPRESSED (Telegram gating anomaly)",
            probable_causes=[
                "Score floor set too high (all signals below threshold)",
                "Kill switch accidentally activated",
                "Regime mismatch persisting across all signals",
                "Telegram bot token expired or rate-limited",
                "Output policy gate has unhandled exception causing fail-closed"
            ],
            severity="HIGH"
        )
    return None
```

---

### 3.4 DRIFT_TELEGRAM_PDF

**Signature:** Telegram is sending messages (gating appears functional) but PDF generation is failing its quality audit.

**Detection Logic:**

```python
def detect_drift_telegram_pdf(
    tg_result: IntegrityResult,
    pdf_result: IntegrityResult
) -> Optional[DriftSignature]:
    tg_ok = len(tg_result.failures) == 0
    pdf_fail = any(f.code == "INTEG_PDF_QA_FAIL" for f in pdf_result.failures)

    if tg_ok and pdf_fail:
        return DriftSignature(
            code="DRIFT_TELEGRAM_PDF",
            description="Telegram is delivering but PDF quality audit is failing",
            upstream_status="HEALTHY (Telegram gating normal)",
            downstream_status="FAILING (PDF QA not passing)",
            probable_causes=[
                "PDF renderer has different data path than Telegram formatter",
                "PDF template rendering error (LaTeX/HTML issue)",
                "PDF QA checker itself has a bug (false negative)",
                "Stale data passing Telegram gates but failing stricter PDF checks"
            ],
            severity="MEDIUM"
        )
    return None
```

---

### 3.5 DRIFT_REGIME_CROSS

**Signature:** Different modules within the system are operating under different regime classifications simultaneously.

**Detection Logic:**

```python
def detect_drift_regime_cross() -> Optional[DriftSignature]:
    # Collect regime from each module's output
    sources = {
        "engine": get_engine_regime(),         # From system_state.json
        "war_room": get_war_room_regime(),     # From /api/health
        "scoring": get_scoring_regime(),       # From last score computation
        "pdf": get_pdf_regime(),               # From last PDF metadata
    }

    # Remove None values (module not yet initialised)
    active = {k: v for k, v in sources.items() if v is not None}

    if len(active) < 2:
        return None  # Not enough sources to compare

    regimes = set(active.values())
    if len(regimes) > 1:
        return DriftSignature(
            code="DRIFT_REGIME_CROSS",
            description="Regime mismatch across modules",
            upstream_status=f"Regimes reported: {active}",
            downstream_status="INCONSISTENT",
            probable_causes=[
                "Regime classifier updated but not all consumers refreshed",
                "Caching layer serving stale regime to some modules",
                "Race condition: regime changed between module read times",
                "One module has a hardcoded or overridden regime value"
            ],
            severity="HIGH"
        )
    return None
```

---

### Drift Signature Summary

| Signature Code | Broken Link | Severity | Auto-Recovery Possible? |
|---|---|---|---|
| `DRIFT_ENGINE_ARTIFACT` | Engine -> Artifacts | HIGH | No -- requires investigation |
| `DRIFT_ARTIFACT_WARROOM` | Artifacts -> War Room | HIGH | Possible (War Room restart via self-healing) |
| `DRIFT_WARROOM_TELEGRAM` | War Room -> Telegram | HIGH | Partial (check kill switch, score floor) |
| `DRIFT_TELEGRAM_PDF` | Telegram -> PDF | MEDIUM | No -- requires investigation |
| `DRIFT_REGIME_CROSS` | Cross-module consistency | HIGH | Possible (wait for next regime refresh cycle) |

---

## 4. Detection Policy

### 4.1 On Drift Detection

When any wiring drift signature is detected, the system executes the following response in strict order:

```
Step 1: SWITCH TO DEGRADED MODE
    |-- Set system_mode = "DEGRADED"
    |-- Write to artifacts/system_state.json: {"mode": "DEGRADED", "reason": drift_code}
    |-- War Room: display DEGRADED banner
    |
    v
Step 2: BLOCK TRADE OUTPUTS
    |-- Suppress all Telegram signals (not system alerts)
    |-- Hold PDF generation (do not produce reports with inconsistent data)
    |-- War Room continues to display with DEGRADED banner
    |-- NOTE: System [SYSTEM] alerts are NEVER blocked
    |
    v
Step 3: SEND INTEGRITY ALERT
    |-- Telegram message:
    |       "[SYSTEM] INTEGRITY ALERT: {drift_signature}
    |        Detected: {timestamp}
    |        Upstream: {upstream_status}
    |        Downstream: {downstream_status}
    |        Probable causes: {causes}
    |        Operator action: see playbook below"
    |
    v
Step 4: WRITE INTEGRITY ALERT ARTIFACT
    |-- Write/append to artifacts/integrity_alert.json:
    |       {
    |           "alert_id": "uuid",
    |           "drift_code": "DRIFT_ENGINE_ARTIFACT",
    |           "detected_at": "ISO8601",
    |           "upstream_status": "...",
    |           "downstream_status": "...",
    |           "probable_causes": ["..."],
    |           "resolved": false,
    |           "resolved_at": null
    |       }
    |
    v
Step 5: PROVIDE OPERATOR PLAYBOOK
    |-- Include drift-specific recovery steps in the Telegram alert
    |-- (See Section 6 for playbooks)
```

### 4.2 Resolution

A drift alert is resolved when:

1. The integrity monitor runs again and the specific drift signature is no longer detected.
2. On resolution, the system:
   - Sets `resolved: true` and `resolved_at: ISO8601` in the alert artifact.
   - Sends a Telegram message: `[SYSTEM] INTEGRITY RESOLVED: {drift_code} cleared at {timestamp}`.
   - Exits DEGRADED mode if no other drift signatures are active.
   - Unblocks trade outputs.

### 4.3 Multiple Simultaneous Drifts

If multiple drift signatures are detected simultaneously:

- ALL are reported in a single Telegram alert (not separate messages).
- Trade outputs remain blocked until ALL drifts resolve.
- DEGRADED mode persists until the last drift clears.
- Each drift signature has its own entry in `integrity_alert.json` and can resolve independently.

### 4.4 Flapping Detection

If a drift signature resolves and re-appears within 10 minutes, it is considered "flapping":

- Flapping drift is escalated with a `[SYSTEM] INTEGRITY FLAPPING` alert.
- The system does NOT rapidly toggle DEGRADED mode. Once DEGRADED for a flapping drift, it stays DEGRADED for a minimum of 15 minutes even if the drift temporarily resolves.
- Flapping count is tracked. If a drift flaps more than 3 times in 1 hour, the system sends a `[SYSTEM] INTEGRITY CRITICAL: Persistent instability` alert.

---

## 5. Implementation

### 5.1 Module Location

```
core/integrity_monitor.py
```

### 5.2 Class Interface

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


@dataclass
class IntegrityFailure:
    code: str
    detail: str
    measured_value: any
    threshold: any


@dataclass
class IntegrityResult:
    check_id: str
    failures: list[IntegrityFailure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0


@dataclass
class DriftSignature:
    code: str
    description: str
    upstream_status: str
    downstream_status: str
    probable_causes: list[str]
    severity: str  # "HIGH" | "MEDIUM"


class IntegrityMonitor:
    """
    Continuous integrity monitor for wiring drift detection.
    Instantiated once in main.py. Called from APScheduler job.
    """

    def __init__(self, config: dict, telegram: TelegramClient):
        self.config = config
        self.telegram = telegram
        self.alert_history: list[dict] = []
        self.flap_tracker: dict[str, list[datetime]] = {}
        self.degraded_until: Optional[datetime] = None

    def run_all_checks(self) -> dict:
        """
        Execute all integrity checks and drift detection.
        Called every 5 min (market) / 15 min (off-hours).

        Returns cumulative integrity status dict, also written
        to artifacts/integrity_status.json.
        """
        # 1. Run individual checks
        scan_result = check_scan_sla()
        art_result = check_artifact_freshness()
        wr_result = check_war_room_freshness()
        tg_result = check_telegram_suppression()
        pdf_result = check_pdf_audit()
        prov_result = check_provider_health()
        disagree_result = check_provider_disagreement()

        # 2. Detect wiring drift signatures
        drifts = []
        for detector in [
            lambda: detect_drift_engine_artifact(scan_result, art_result),
            lambda: detect_drift_artifact_warroom(art_result, wr_result),
            lambda: detect_drift_warroom_telegram(wr_result, tg_result),
            lambda: detect_drift_telegram_pdf(tg_result, pdf_result),
            lambda: detect_drift_regime_cross(),
        ]:
            drift = detector()
            if drift is not None:
                drifts.append(drift)

        # 3. Apply detection policy
        if drifts:
            self._handle_drifts(drifts)
        else:
            self._check_resolution()

        # 4. Write cumulative status
        status = {
            "timestamp": now_iso(),
            "mode": "DEGRADED" if drifts else "NORMAL",
            "checks": {
                "scan": scan_result.__dict__,
                "artifacts": art_result.__dict__,
                "war_room": wr_result.__dict__,
                "telegram": tg_result.__dict__,
                "pdf": pdf_result.__dict__,
                "providers": prov_result.__dict__,
                "disagreement": disagree_result.__dict__,
            },
            "active_drifts": [d.__dict__ for d in drifts],
            "alert_history": self.alert_history[-50:],
        }

        write_json("artifacts/integrity_status.json", status)
        return status

    def _handle_drifts(self, drifts: list[DriftSignature]) -> None:
        """Apply detection policy for discovered drifts."""
        ...

    def _check_resolution(self) -> None:
        """Check if previously active drifts have resolved."""
        ...

    def _check_flapping(self, drift_code: str) -> bool:
        """Returns True if this drift is flapping."""
        ...
```

### 5.3 APScheduler Registration

```python
# In main.py, after engine initialisation:

integrity = IntegrityMonitor(config, telegram)

def get_integrity_interval():
    """Return 5 minutes during market hours, 15 minutes outside."""
    if is_market_hours(datetime.now(timezone.utc)):
        return 5
    return 15

scheduler.add_job(
    integrity.run_all_checks,
    trigger="interval",
    minutes=5,  # Base interval; adjusted dynamically
    id="integrity_monitor",
    name="Continuous Integrity Monitor",
    max_instances=1,
    coalesce=True
)
```

### 5.4 War Room API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/integrity/status` | GET | Current integrity status: all check results, active drifts, mode |
| `GET /api/integrity/history` | GET | Last 50 integrity alerts with resolution status |
| `GET /api/integrity/drifts` | GET | Currently active drift signatures with playbook links |

### 5.5 Integration with Self-Healing (NZT48-ANNEX-SHO-001)

The integrity monitor and self-healing controller are complementary but independent:

| Concern | Integrity Monitor | Self-Healing Controller |
|---|---|---|
| **Detects** | Wiring drift between components | Individual component failures |
| **Responds** | DEGRADED mode + operator alert | Auto-restart, cache eviction, reconnect |
| **Modifies state** | No (read-only checks, writes status artifact) | Yes (restarts processes, evicts cache) |
| **Blocks outputs** | Yes (on drift detection) | No (escalates, does not block) |

When a drift is detected that could be resolved by a self-healing action (e.g., `DRIFT_ARTIFACT_WARROOM` may be resolved by a War Room restart), the integrity monitor does NOT trigger the self-healing action directly. Instead, the self-healing controller independently detects the War Room failure via its own health checks and acts accordingly. The integrity monitor observes the result on its next cycle.

---

## 6. Operator Playbooks

### 6.1 DRIFT_ENGINE_ARTIFACT Playbook

```
DRIFT: Engine running but artifacts not written

DIAGNOSIS:
1. SSH into EC2: ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28
2. Check engine logs for write errors:
   docker logs nzt48 --tail 100 | grep -i "artifact\|write\|permission\|disk"
3. Check disk space:
   docker exec nzt48 df -h /data/artifacts/
4. Check artifact directory permissions:
   docker exec nzt48 ls -la /data/artifacts/
5. Check for Python exceptions in artifact write path:
   docker logs nzt48 --tail 200 | grep -i "traceback\|error\|exception"

RESOLUTION:
- If disk full: clear old logs (logs/*.log.1 through .5), then wait for next scan cycle.
- If permission error: docker exec nzt48 chmod 755 /data/artifacts/
- If exception in write path: inspect the traceback, fix code, rebuild:
  docker-compose build nzt48 && docker-compose up -d nzt48
- If APScheduler job stopped: restart engine (requires operator approval per SHO-001).

VERIFICATION:
- Wait for next integrity check (5 min). Drift should auto-resolve.
- Confirm: docker exec nzt48 cat artifacts/system_state.json | python -m json.tool
```

### 6.2 DRIFT_ARTIFACT_WARROOM Playbook

```
DRIFT: Artifacts fresh but War Room stale

DIAGNOSIS:
1. Check War Room process:
   docker exec nzt48 ps aux | grep uvicorn
2. Check War Room logs:
   docker logs nzt48-dashboard --tail 100
3. Test War Room directly:
   curl http://localhost:3001/api/health | python -m json.tool
4. Check if War Room is reading correct artifact path:
   docker exec nzt48-dashboard env | grep ARTIFACT

RESOLUTION:
- If War Room process hung: self-healing controller should auto-restart (SHO-001 2.2).
  If cooldown active, manually restart:
  docker-compose restart nzt48-dashboard
- If wrong artifact path: fix environment variable, rebuild dashboard:
  docker-compose build nzt48-dashboard && docker-compose up -d nzt48-dashboard
- If file watcher broken: restart War Room (restarts file watcher).

VERIFICATION:
- curl http://localhost:3001/api/health -- last_update should be within 300s.
- Wait for next integrity check. Drift should auto-resolve.
```

### 6.3 DRIFT_WARROOM_TELEGRAM Playbook

```
DRIFT: War Room healthy but Telegram excessively suppressed

DIAGNOSIS:
1. Check kill switch status:
   curl http://localhost:8000/api/status | python -m json.tool
   (Look for kill_switch.active)
2. Check output policy state:
   docker exec nzt48 cat /data/nzt48_output_state.json | python -m json.tool
3. Check score distribution:
   docker logs nzt48 --tail 200 | grep "score_floor\|SUPPRESS"
4. Check regime consistency:
   docker logs nzt48 --tail 100 | grep "REGIME_MISMATCH"
5. Check Telegram bot token:
   docker exec nzt48 python -c "import requests; r=requests.get('https://api.telegram.org/bot{TOKEN}/getMe'); print(r.status_code)"

RESOLUTION:
- If kill switch active: deactivate if appropriate:
  POST /api/override/kill-switch-off (requires API key + reason)
- If score floor too high: review config/settings.yaml score_floor parameter.
- If regime mismatch persistent: check regime classifier output.
- If Telegram token expired: update token in environment, restart engine.

VERIFICATION:
- Trigger a test signal. Confirm it reaches Telegram.
- Wait for next integrity check. Drift should auto-resolve.
```

### 6.4 DRIFT_TELEGRAM_PDF Playbook

```
DRIFT: Telegram delivering but PDF QA failing

DIAGNOSIS:
1. Check last PDF QA result:
   docker exec nzt48 cat artifacts/pdf_audit.json | python -m json.tool
2. Check PDF generation logs:
   docker logs nzt48 --tail 100 | grep -i "pdf\|render\|latex\|html"
3. Manually trigger a PDF and inspect output:
   docker exec nzt48 python -c "from delivery.pdf_v2_momentum import generate; generate()"
4. Check if PDF data path differs from Telegram data path:
   Compare data sources in pdf_v2_momentum.py vs telegram_formatter.py

RESOLUTION:
- If rendering error: inspect template, fix HTML/CSS issue, rebuild.
- If data path divergence: align PDF data source with Telegram data source.
- If QA checker has false negative: review QA criteria in pdf_audit module.

VERIFICATION:
- Manually generate a PDF. Run QA check. Confirm PASS.
- Wait for next scheduled PDF. Drift should auto-resolve.
```

### 6.5 DRIFT_REGIME_CROSS Playbook

```
DRIFT: Regime mismatch across modules

DIAGNOSIS:
1. Check regime from each source:
   docker exec nzt48 python -c "
   import json
   state = json.load(open('artifacts/system_state.json'))
   print('Engine regime:', state.get('regime'))
   "
   curl http://localhost:3001/api/health | python -m json.tool | grep regime
2. Check regime update timestamps across modules:
   docker logs nzt48 --tail 50 | grep "regime"
3. Check for caching issues:
   docker exec nzt48 python -c "from core.cache import get_regime_cache; print(get_regime_cache())"

RESOLUTION:
- If race condition: wait for next scan cycle (60s). Regime should converge.
- If caching: flush regime cache:
  docker exec nzt48 python -c "from core.cache import flush_regime; flush_regime()"
- If hardcoded override: search for manual regime overrides in config/settings.yaml.

VERIFICATION:
- Wait 2-3 scan cycles (3 minutes). Regime should converge across all modules.
- If not converging: restart engine (requires operator approval per SHO-001).
```

---

## 7. Acceptance Tests

### T-INTEG-001: Scan SLA Breach Detection

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running normally. All integrity checks passing. |
| **Action** | Freeze the engine scan loop (simulate by pausing APScheduler). Wait 3 minutes (180s). Run integrity monitor. |
| **Expected** | `INTEG_SCAN_ENGINE_STALE` detected. If artifacts are still fresh from the last scan, no drift signature yet. If artifacts also go stale, `DRIFT_ENGINE_ARTIFACT` detected. |
| **Pass Criteria** | `INTEG_SCAN_ENGINE_STALE` in check results. `measured_value` > 180. `threshold` = 180. |

### T-INTEG-002: Artifact Staleness Detection

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running. Artifacts being written normally. |
| **Action** | Stop artifact writes (mock the write function to no-op). Wait 5 minutes. Run integrity monitor. |
| **Expected** | `INTEG_ART_SYSTEM_STATE_STALE`, `INTEG_ART_SCAN_HEALTH_STALE` detected. `DRIFT_ENGINE_ARTIFACT` drift signature detected (engine running but no artifacts). |
| **Pass Criteria** | Artifact staleness failures present. Drift signature `DRIFT_ENGINE_ARTIFACT` detected. System enters DEGRADED mode. Telegram `[SYSTEM]` alert sent. `integrity_alert.json` written. |

### T-INTEG-003: War Room Staleness with Fresh Artifacts

| Aspect | Detail |
|---|---|
| **Precondition** | Engine and artifacts healthy. War Room running. |
| **Action** | Freeze War Room's artifact reader (simulate stale state by mocking `/api/health` to return old `last_update`). Run integrity monitor. |
| **Expected** | `INTEG_WR_STALE` detected. `DRIFT_ARTIFACT_WARROOM` drift signature detected. DEGRADED mode entered. |
| **Pass Criteria** | Drift signature present. Telegram alert sent with correct playbook. `integrity_alert.json` contains entry with `drift_code: DRIFT_ARTIFACT_WARROOM`. |

### T-INTEG-004: Telegram High Suppression Detection

| Aspect | Detail |
|---|---|
| **Precondition** | Engine running. War Room healthy. |
| **Action** | Inject 6 invalid-score suppressions within 60 minutes into the suppression counter. Run integrity monitor. |
| **Expected** | `INTEG_TG_HIGH_SUPPRESSION` detected. `DRIFT_WARROOM_TELEGRAM` drift signature detected (War Room healthy but Telegram suppressing). |
| **Pass Criteria** | `measured_value` = 6, `threshold` = 5. Drift signature present. |

### T-INTEG-005: Provider Degradation Detection

| Aspect | Detail |
|---|---|
| **Precondition** | All providers healthy. |
| **Action** | Set 3 of 5 active providers to status `DOWN` in the provider health registry. Run integrity monitor. |
| **Expected** | `INTEG_PROV_DEGRADED` detected with OK rate = 40% (2/5). |
| **Pass Criteria** | `measured_value` = 0.4, `threshold` = 0.80. Failed provider names listed in detail. |

### T-INTEG-006: Provider Price Disagreement

| Aspect | Detail |
|---|---|
| **Precondition** | Two providers reporting prices for QQQ3.L. |
| **Action** | Inject provenance records: yfinance reports close=100.00, twelve_data reports close=103.00 (3% divergence). Run integrity monitor. |
| **Expected** | `INTEG_DISAGREE_PRICE` detected for QQQ3.L. |
| **Pass Criteria** | `measured_value` = 0.03 (3%), `threshold` = 0.02 (2%). Ticker, providers, and prices in detail string. |

### T-INTEG-007: Regime Cross-Module Drift

| Aspect | Detail |
|---|---|
| **Precondition** | All modules reporting regime. |
| **Action** | Set engine regime to `EXPANSION` and War Room regime to `CONTRACTION` (simulate by modifying system_state.json but not refreshing War Room). Run integrity monitor. |
| **Expected** | `DRIFT_REGIME_CROSS` detected. All module regimes listed in the signature. |
| **Pass Criteria** | Drift signature present with severity `HIGH`. Probable causes include caching and race condition. |

### T-INTEG-008: DEGRADED Mode Entry and Trade Output Blocking

| Aspect | Detail |
|---|---|
| **Precondition** | System in NORMAL mode. |
| **Action** | Trigger any drift signature. Attempt to send a Telegram signal. Attempt to generate a PDF. |
| **Expected** | Telegram signal is blocked (suppressed). PDF generation is held. War Room displays DEGRADED banner. System `[SYSTEM]` alerts are NOT blocked. |
| **Pass Criteria** | Signal suppression logged. PDF deferred. War Room banner active. System alerts delivered. |

### T-INTEG-009: Drift Resolution and Mode Recovery

| Aspect | Detail |
|---|---|
| **Precondition** | System in DEGRADED mode due to `DRIFT_ENGINE_ARTIFACT`. |
| **Action** | Fix the artifact write path (restore normal writes). Wait for next integrity check. |
| **Expected** | Drift signature no longer detected. `resolved: true` and `resolved_at` set in integrity_alert.json. Telegram sends `[SYSTEM] INTEGRITY RESOLVED`. System exits DEGRADED mode. Trade outputs unblocked. |
| **Pass Criteria** | Mode returns to NORMAL. Alert marked resolved. Telegram resolution message sent. |

### T-INTEG-010: Flapping Detection

| Aspect | Detail |
|---|---|
| **Precondition** | System in NORMAL mode. |
| **Action** | Trigger `DRIFT_ENGINE_ARTIFACT`. Wait for resolution (fix writes). Wait 5 minutes. Trigger the same drift again. Wait for resolution. Trigger again within 10 minutes. |
| **Expected** | On 3rd occurrence within 1 hour, system detects flapping. Sends `[SYSTEM] INTEGRITY FLAPPING` alert. DEGRADED mode persists for minimum 15 minutes even if drift temporarily resolves. |
| **Pass Criteria** | Flapping alert sent. DEGRADED mode held for 15 minutes minimum. Flap count tracked and reported. |

### Acceptance Test Summary

| Test ID | Category | Key Assertion |
|---|---|---|
| T-INTEG-001 | Scan SLA | Engine staleness detected with correct thresholds |
| T-INTEG-002 | Artifact + Drift | Artifact staleness triggers `DRIFT_ENGINE_ARTIFACT` |
| T-INTEG-003 | War Room + Drift | War Room staleness triggers `DRIFT_ARTIFACT_WARROOM` |
| T-INTEG-004 | Telegram + Drift | High suppression triggers `DRIFT_WARROOM_TELEGRAM` |
| T-INTEG-005 | Provider Health | Provider degradation detected at 80% threshold |
| T-INTEG-006 | Provider Disagreement | Price divergence > 2% detected per ticker |
| T-INTEG-007 | Regime Drift | Cross-module regime mismatch detected |
| T-INTEG-008 | DEGRADED Mode | Trade outputs blocked, system alerts unblocked |
| T-INTEG-009 | Resolution | Drift clears, mode recovers, alert marked resolved |
| T-INTEG-010 | Flapping | Repeated drift toggles detected and escalated |

---

## 8. Proof Artifacts

### 8.1 Output Files

| Artifact | Location | Format | Content |
|---|---|---|---|
| Integrity status (cumulative) | `artifacts/integrity_status.json` | JSON | All check results, active drifts, mode, last 50 alerts |
| Integrity alerts | `artifacts/integrity_alert.json` | JSON array | Active and recently resolved drift alerts |
| Integrity monitor log | `logs/integrity_monitor.log` | JSON lines | Every check cycle result, drift detections, resolutions |

### 8.2 Integrity Status JSON Schema

```json
{
    "timestamp": "2026-02-27T14:35:00Z",
    "mode": "NORMAL",
    "checks": {
        "scan": {
            "check_id": "INTEG-SCAN",
            "failures": []
        },
        "artifacts": {
            "check_id": "INTEG-ART",
            "failures": []
        },
        "war_room": {
            "check_id": "INTEG-WR",
            "failures": []
        },
        "telegram": {
            "check_id": "INTEG-TG",
            "failures": []
        },
        "pdf": {
            "check_id": "INTEG-PDF",
            "failures": []
        },
        "providers": {
            "check_id": "INTEG-PROV",
            "failures": []
        },
        "disagreement": {
            "check_id": "INTEG-DISAGREE",
            "failures": []
        }
    },
    "active_drifts": [],
    "flapping_drifts": [],
    "degraded_since": null,
    "last_resolution": null,
    "alert_history": []
}
```

### 8.3 Integrity Alert JSON Schema

```json
{
    "alert_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "drift_code": "DRIFT_ENGINE_ARTIFACT",
    "detected_at": "2026-02-27T14:35:00Z",
    "severity": "HIGH",
    "upstream_status": "HEALTHY (scan loop active)",
    "downstream_status": "STALE (system_state.json, scan_health.json)",
    "probable_causes": [
        "Artifact write path has an unhandled exception",
        "Disk full or permission error on artifacts/ directory"
    ],
    "resolved": false,
    "resolved_at": null,
    "flap_count": 0,
    "operator_notified_at": "2026-02-27T14:35:01Z"
}
```

### 8.4 Monitoring Metrics

| Metric | Description | Alert Threshold |
|---|---|---|
| `integrity_check_duration_ms` | Time to execute all checks | Alert if > 10000ms |
| `integrity_drifts_active` | Count of active drift signatures | Alert if > 0 |
| `integrity_mode` | Current mode (NORMAL / DEGRADED) | Alert if DEGRADED for > 30 min |
| `integrity_flap_count` | Flapping drift count in rolling 1h | Alert if > 3 |
| `integrity_check_failures` | Individual check failures per cycle | Informational |
| `integrity_resolution_time_avg` | Average time from drift detection to resolution | Informational |

### 8.5 Compliance Verification

```bash
# Verify integrity monitor module exists
docker exec nzt48 python -c "from core.integrity_monitor import IntegrityMonitor; print('OK')"

# Verify APScheduler job is registered
docker exec nzt48 python -c "
from main import scheduler
jobs = scheduler.get_jobs()
integ = [j for j in jobs if j.id == 'integrity_monitor']
print(f'Integrity monitor job: {integ[0].name if integ else \"NOT FOUND\"}')"

# Check current integrity status
docker exec nzt48 cat artifacts/integrity_status.json | python -m json.tool

# Check for active alerts
docker exec nzt48 cat artifacts/integrity_alert.json | python -m json.tool

# View recent integrity log entries
docker exec nzt48 tail -10 logs/integrity_monitor.log

# Run acceptance test suite
docker exec nzt48 pytest tests/test_integrity_monitor.py -v --tb=short
```

---

## 9. Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-02-27 | NZT-48 System | Initial specification -- 7 integrity checks, 5 wiring drift signatures, detection policy, operator playbooks defined |

---

**END OF DOCUMENT**

This specification is binding. The integrity monitor must run on the defined schedule. Any wiring drift detection must trigger the full detection policy cascade. No drift signature may be silently ignored. No exceptions.
