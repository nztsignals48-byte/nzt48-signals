# Artifact Single-Source Policy

| Field          | Value                    |
|----------------|--------------------------|
| Document ID    | NZT48-ANNEX-ASP-001      |
| Version        | 1.0                      |
| Date           | 2026-02-27               |
| Status         | **BINDING**              |
| Classification | Internal / Engineering   |

---

## 1. OBJECTIVE

Telegram, PDFs, and War Room **MUST** render from the same artifacts for a given `run_id`. No component may recompute data independently.

Every consumer of scan-cycle output reads from a single, immutable artifact set written once by the engine. This eliminates divergence between what the PDF says, what Telegram sends, and what the War Room displays.

---

## 2. THE RULE

For any given scan cycle (identified by `run_id`):

| Step | Actor             | Action                                                    | Source              |
|------|-------------------|-----------------------------------------------------------|---------------------|
| 1    | Engine            | Writes artifacts **ONCE** to `artifacts/` directory       | Engine computation  |
| 2    | Telegram Bot      | Reads **FROM** those artifacts                            | `artifacts/`        |
| 3    | PDF Renderer      | Reads **FROM** those artifacts                            | `artifacts/`        |
| 4    | War Room API      | Reads **FROM** those artifacts                            | `artifacts/`        |

### Failure Mode

If artifacts are **missing** or **schema-invalid**, outputs **MUST** be blocked entirely, or downgraded to **SYSTEM HEALTH ONLY** mode (no plays, no scores, no regime labels).

```
IF artifact_missing(run_id) OR schema_invalid(run_id):
    mode = "SYSTEM_HEALTH_ONLY"
    plays_output = BLOCKED
    regime_output = BLOCKED
    health_output = ALLOWED
    EMIT integrity_alert(run_id, reason)
```

---

## 3. RUN MANIFEST CONCEPT

Each scan cycle produces a manifest file that serves as the **cryptographic receipt** proving what was computed, from what inputs, and at what time.

### Manifest Location

```
artifacts/manifests/manifest_{run_id}.json
```

### Manifest Schema

| Field             | Type       | Description                                          |
|-------------------|------------|------------------------------------------------------|
| `run_id`          | `string`   | UUID v4 identifying this scan cycle                  |
| `timestamp`       | `string`   | ISO-8601 UTC timestamp of cycle completion           |
| `git_hash`        | `string`   | Short SHA of current deployed commit                 |
| `config_hash`     | `string`   | SHA-256 of `settings.yaml` at cycle start            |
| `universe_hash`   | `string`   | SHA-256 of universe tickers list (sorted, joined)    |
| `providers_used`  | `string[]` | List of data providers invoked (e.g. `["yfinance"]`) |
| `as_of_times`     | `object`   | Per-provider last-data timestamps                    |
| `schema_versions` | `object`   | Per-artifact schema version (e.g. `{"plays": "1.0"}`) |
| `plays_count`     | `int`      | Total plays generated this cycle                     |
| `signals_strict`  | `int`      | Plays meeting strict threshold                       |
| `signals_fallback`| `int`      | Plays meeting fallback threshold only                |
| `drought_flag`    | `bool`     | `true` if no actionable plays found                  |

### Manifest Example

```json
{
  "run_id": "a3f7c291-4e12-4b8a-9d5f-1c2e3f4a5b6c",
  "timestamp": "2026-02-27T14:30:00Z",
  "git_hash": "e4b2f1a",
  "config_hash": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "universe_hash": "sha256:3c7a5b2e1f4d8a6c9b0e2d4f6a8c0e2d4f6a8c0e2d4f6a8c0e2d4f6a8c0e2d",
  "providers_used": ["yfinance"],
  "as_of_times": {"yfinance": "2026-02-27T14:29:55Z"},
  "schema_versions": {"plays": "1.0", "system_state": "1.0", "scan_health": "1.0", "drought": "1.0"},
  "plays_count": 3,
  "signals_strict": 1,
  "signals_fallback": 2,
  "drought_flag": false
}
```

### Consumer Validation

Any consumer can validate alignment by checking:

```python
def validate_source_alignment(consumer_run_id: str, manifest_path: str) -> bool:
    manifest = json.load(open(manifest_path))
    return consumer_run_id == manifest["run_id"]
```

If `run_id` does not match, the consumer **MUST** refuse to render and emit an `INTEGRITY_ALERT`.

---

## 4. SCHEMA REGISTRY

Every artifact has a defined schema. Validation runs on **every write** (engine side) and **every read** (consumer side). Schema mismatch triggers output block and integrity alert.

### 4.1 `plays.json`

```json
{
  "run_id": "string (UUID)",
  "session": "string (pre_market | us_open | uk_open | continuous)",
  "timestamp": "string (ISO-8601)",
  "plays": [
    {
      "ticker": "string",
      "direction": "string (LONG | SHORT)",
      "entry": "float",
      "stop": "float",
      "target": "float",
      "score": "float (0.0 - 1.0)",
      "confidence": "string (HIGH | MEDIUM | LOW)",
      "tier": "string (STRICT | FALLBACK)",
      "strategy": "string (S1-S15 identifier)",
      "regime": "string",
      "reasoning": "string",
      "atr_multiple": "float",
      "risk_reward_ratio": "float"
    }
  ]
}
```

### 4.2 `system_state.json`

```json
{
  "run_id": "string (UUID)",
  "timestamp": "string (ISO-8601)",
  "mode": "string (LIVE_PAPER | LIVE_REAL | MAINTENANCE | DEGRADED)",
  "regime": "string (RISK_ON | RISK_OFF | CHOPPY | TRENDING | UNKNOWN)",
  "regime_confidence": "float (0.0 - 1.0)",
  "data_reliability": "string (FULL | PARTIAL | DEGRADED | OFFLINE)",
  "drought_flag": "bool",
  "drought_cycles": "int",
  "open_positions": [
    {
      "ticker": "string",
      "direction": "string",
      "entry_price": "float",
      "current_price": "float",
      "unrealised_pnl_pct": "float",
      "opened_at": "string (ISO-8601)"
    }
  ],
  "equity_snapshot": {
    "total": "float",
    "cash": "float",
    "invested": "float",
    "daily_pnl_pct": "float"
  }
}
```

### 4.3 `scan_health.json`

```json
{
  "run_id": "string (UUID)",
  "timestamp": "string (ISO-8601)",
  "last_tick_time": "string (ISO-8601)",
  "engine_runs_today": "int",
  "p95_cycle_ms": "float",
  "p99_cycle_ms": "float",
  "avg_cycle_ms": "float",
  "errors_last_hour": "int",
  "provider_status": {
    "yfinance": "string (OK | DEGRADED | DOWN)"
  },
  "memory_mb": "float",
  "cpu_pct": "float"
}
```

### 4.4 `drought.json`

```json
{
  "run_id": "string (UUID)",
  "timestamp": "string (ISO-8601)",
  "state": "string (NORMAL | WATCH | DROUGHT | SEVERE_DROUGHT)",
  "cycles_empty": "int",
  "hours_empty": "float",
  "escalation_tier": "int (0-3)",
  "last_play_run_id": "string (UUID) | null",
  "last_play_timestamp": "string (ISO-8601) | null",
  "relaxation_applied": "bool",
  "thresholds_current": {
    "min_score": "float",
    "min_confidence": "string"
  }
}
```

### Schema Validation Flow

```
ENGINE WRITE                          CONSUMER READ
    |                                      |
    v                                      v
[Compute artifacts]                [Load artifact file]
    |                                      |
    v                                      v
[Validate against schema]          [Validate against schema]
    |                                      |
    +-- PASS --> Write to disk             +-- PASS --> Render output
    |                                      |
    +-- FAIL --> ABORT cycle               +-- FAIL --> BLOCK output
               + INTEGRITY_ALERT                      + INTEGRITY_ALERT
                                                      + SYSTEM_HEALTH_ONLY mode
```

---

## 5. ANTI-PATTERNS (FORBIDDEN)

The following patterns are **categorically forbidden**. Any code review that finds these patterns **MUST** block the merge.

| ID       | Anti-Pattern                                                        | Why It Is Forbidden                                       | Correct Approach                                |
|----------|---------------------------------------------------------------------|-----------------------------------------------------------|-------------------------------------------------|
| AP-001   | PDF code fetching live prices via `yfinance` during render          | Creates price divergence between PDF and Telegram          | Read `plays.json` entry/stop/target fields      |
| AP-002   | Telegram bot computing its own regime label                         | Regime label may differ from what engine computed           | Read `system_state.json` `regime` field         |
| AP-003   | War Room API running its own scoring engine                         | Scores will drift from artifact scores                     | Serve `plays.json` directly                     |
| AP-004   | Any module caching regime/confidence/drought independently          | Stale cache creates phantom divergence                     | Always read from `system_state.json` on demand  |
| AP-005   | Consumer writing back to artifact files                             | Artifacts are immutable once written                       | Consumers are read-only                         |
| AP-006   | Rendering from `run_id` N while artifacts are from `run_id` N-1    | Temporal mismatch produces inconsistent output             | Validate `run_id` match before render           |
| AP-007   | Hardcoding regime thresholds in consumer code                       | Thresholds belong in `settings.yaml` and flow via artifacts | Read thresholds from artifacts or config        |
| AP-008   | Multiple engine threads writing artifacts concurrently              | Race condition corrupts artifact files                     | Single-writer lock on artifact directory        |

### Code Review Checklist

Before any PR is merged, reviewers must verify:

- [ ] No `yfinance` imports in `delivery/` directory (except clearly marked cache-warming utilities)
- [ ] No `regime =` assignments in `delivery/` or `api/` directories
- [ ] No `score =` computations in `delivery/` or `api/` directories
- [ ] All artifact reads validate `run_id` matches expected cycle
- [ ] All artifact reads validate schema before use
- [ ] No `open()` calls with write mode (`'w'`, `'a'`) in consumer modules

---

## 6. ACCEPTANCE TESTS

### T-SSP-001: Single-Source Write Verification

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify engine writes all artifacts exactly once per cycle             |
| **Procedure**  | 1. Start engine in paper mode                                        |
|                | 2. Trigger one scan cycle                                            |
|                | 3. Count write operations to `artifacts/` directory                  |
|                | 4. Verify each artifact file has identical `run_id`                  |
| **Pass**       | Each artifact written exactly once; all `run_id` fields match        |
| **Fail**       | Any artifact written more than once, or `run_id` mismatch            |

### T-SSP-002: Consumer Source Validation

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify all consumers read from artifacts, not independent computation |
| **Procedure**  | 1. Complete a scan cycle producing artifacts with `run_id` = X       |
|                | 2. Manually corrupt `plays.json` by changing one ticker name         |
|                | 3. Trigger Telegram send, PDF render, and War Room refresh           |
|                | 4. Verify all three show the corrupted ticker name                   |
| **Pass**       | All three consumers display the corrupted data (proving artifact read) |
| **Fail**       | Any consumer shows the original (uncorrupted) ticker                 |

### T-SSP-003: Schema Validation on Write

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify engine blocks artifact write when schema is violated           |
| **Procedure**  | 1. Temporarily modify engine to produce a play with missing `ticker`  |
|                | 2. Run scan cycle                                                    |
|                | 3. Check that artifact write is aborted                              |
|                | 4. Check that `INTEGRITY_ALERT` is emitted                          |
| **Pass**       | Artifact not written; alert logged; previous valid artifact preserved |
| **Fail**       | Invalid artifact written to disk                                     |

### T-SSP-004: Schema Validation on Read

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify consumers block output when artifact schema is invalid         |
| **Procedure**  | 1. Write a valid artifact set                                        |
|                | 2. Manually corrupt `system_state.json` (remove `regime` field)      |
|                | 3. Trigger Telegram send                                             |
|                | 4. Verify Telegram falls back to SYSTEM_HEALTH_ONLY                  |
| **Pass**       | No regime label sent; health-only message delivered; alert logged     |
| **Fail**       | Telegram sends fabricated regime label or crashes                     |

### T-SSP-005: Manifest Integrity

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify manifest accurately records cycle metadata                    |
| **Procedure**  | 1. Record current `git_hash` and `settings.yaml` SHA-256             |
|                | 2. Run scan cycle                                                    |
|                | 3. Read manifest file                                                |
|                | 4. Compare manifest hashes against recorded values                   |
| **Pass**       | `git_hash`, `config_hash`, and `universe_hash` all match             |
| **Fail**       | Any hash mismatch between manifest and actual values                 |

### T-SSP-006: Run ID Mismatch Rejection

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify consumers reject artifacts from a different run_id             |
| **Procedure**  | 1. Complete cycle A producing artifacts with `run_id` = A            |
|                | 2. Complete cycle B producing artifacts with `run_id` = B            |
|                | 3. Replace `plays.json` from cycle B with `plays.json` from cycle A |
|                | 4. Trigger consumers                                                |
| **Pass**       | Consumers detect `run_id` mismatch, block output, emit alert         |
| **Fail**       | Consumers render stale plays without detecting mismatch              |

---

## APPENDIX A: Implementation Priority

| Priority | Component                     | Effort   | Impact   |
|----------|-------------------------------|----------|----------|
| P0       | Artifact write with schema    | Medium   | Critical |
| P0       | Manifest generation           | Low      | Critical |
| P1       | Consumer read with validation | Medium   | High     |
| P1       | Anti-pattern linting rules    | Low      | High     |
| P2       | Automated acceptance tests    | High     | Medium   |

---

*End of Document NZT48-ANNEX-ASP-001*
