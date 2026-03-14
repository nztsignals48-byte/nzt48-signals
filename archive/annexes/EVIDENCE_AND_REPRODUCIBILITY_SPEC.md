# Evidence & Reproducibility Specification

| Field          | Value                                  |
|----------------|----------------------------------------|
| Document ID    | NZT48-ANNEX-ERS-001                    |
| Version        | 1.0                                    |
| Date           | 2026-02-27                             |
| Status         | **BINDING**                            |
| Classification | Internal / Governance                  |
| Owner          | NZT-48 Trading Engine                  |
| Supersedes     | None                                   |

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Run Manifest Specification](#2-run-manifest-specification)
3. [Evidence Chain for Signals](#3-evidence-chain-for-signals)
4. [Evidence Chain for Trades](#4-evidence-chain-for-trades)
5. [Hash Verification](#5-hash-verification)
6. [Data Provenance Chain](#6-data-provenance-chain)
7. [Replay Rules](#7-replay-rules)
8. [Retention Policy](#8-retention-policy)
9. [Evidence Index Requirements](#9-evidence-index-requirements)
10. [Acceptance Tests](#10-acceptance-tests)
11. [Revision History](#11-revision-history)

---

## 1. PURPOSE

This specification establishes a hedge-fund standard evidence trail for the NZT-48 trading system. The governing principle is absolute:

**Every claim is backed by evidence. Every signal is traceable to inputs. Every trade is traceable to a signal. Every scan cycle is reproducible.**

No signal may be generated without a complete evidence pack. No trade may be opened without a traceable signal. No scan cycle may complete without a run manifest. No artifact may exist without a verifiable hash. Violations of these requirements constitute system integrity failures and must trigger alerts per NZT48-ANNEX-CIM-001 (Continuous Integrity Monitor Specification).

### Scope

This specification governs:

- Run manifests produced by every scan cycle
- Signal evidence packs linking signals to their generating inputs
- Trade evidence packs linking trades to their originating signals
- Cryptographic hash verification of all artifacts
- Data provenance chains linking every price and indicator to its source
- Deterministic replay capability for any historical scan cycle
- Retention and archival policy for all evidence artifacts
- The master evidence index cross-referencing all claims to proof

### Relationship to Other Specifications

| Specification | Document ID | Relationship |
|---|---|---|
| Artifact Single-Source Policy | NZT48-ANNEX-ASP-001 | ERS extends the run manifest schema defined in ASP-001 Section 3. ERS manifests are a superset of ASP manifests. |
| Data Provenance Specification | PROVENANCE_SPEC.md | ERS Section 6 references and depends on provenance records defined in the Provenance Spec. ERS does not redefine provenance; it requires provenance chains to be complete and linkable. |
| Continuous Integrity Monitor | NZT48-ANNEX-CIM-001 | Integrity monitor verifies that ERS requirements are being met in real time. Hash mismatches and missing evidence packs trigger CIM alerts. |
| Forensics Map | FORENSICS_MAP.md | Forensics Map identifies risk areas; ERS provides the evidence framework that enables forensic investigation of those risks. |

---

## 2. RUN MANIFEST SPECIFICATION

Every scan cycle produces exactly one run manifest. The manifest is the cryptographic receipt proving what was computed, from what inputs, with what code, and at what time. Manifests are written atomically: either the complete manifest is written or no manifest is written.

### 2.1 Location

```
artifacts/manifests/manifest_{run_id}.json
```

Where `run_id` is a UUID v4 generated at the start of each scan cycle.

### 2.2 Required Fields

The manifest schema extends NZT48-ANNEX-ASP-001 Section 3 with additional fields required for full reproducibility.

#### Identity Fields

| Field | Type | Description | Example |
|---|---|---|---|
| `run_id` | `string (UUID v4)` | Unique identifier for this scan cycle | `"a3f7c291-4e12-4b8a-9d5f-1c2e3f4a5b6c"` |
| `timestamp_utc` | `string (ISO-8601)` | UTC timestamp of manifest generation | `"2026-02-27T14:30:00Z"` |
| `git_hash` | `string` | Short SHA of the deployed commit | `"e4b2f1a"` |
| `config_hash` | `string` | SHA-256 of `settings.yaml` at cycle start | `"sha256:9f86d081..."` |
| `universe_hash` | `string` | SHA-256 of active ticker list (sorted, joined) | `"sha256:3c7a5b2e..."` |
| `engine_version` | `string (semver)` | Engine version string | `"2.0.0"` |
| `python_version` | `string` | Python interpreter version | `"3.11.7"` |
| `container_image_tag` | `string` | Docker image tag of the running container | `"nzt48:2026-02-27-e4b2f1a"` |

#### Input Section

| Field | Type | Description |
|---|---|---|
| `data_providers_used` | `array[object]` | List of providers invoked, each with `{provider, status, latency_ms, fields_fetched, fallback_used}` |
| `tickers_scanned` | `array[string]` | Ordered list of tickers scanned in this cycle |
| `regime_at_scan` | `string` | Regime classification at time of scan (e.g., `EXPANSION`, `COMPRESSION`) |
| `market_phase` | `string` | Market session phase: `PRE_MARKET`, `UK_OPEN`, `US_OPEN`, `CONTINUOUS`, `POST_MARKET`, `CLOSED` |
| `data_coverage_pct` | `float (0-100)` | Percentage of requested data fields that returned valid values |

#### Output Section

| Field | Type | Description |
|---|---|---|
| `plays_generated` | `int` | Total plays generated (all tiers) |
| `signals_strict` | `int` | Plays meeting strict threshold (composite >= 65) |
| `signals_fallback` | `int` | Plays meeting fallback threshold only (composite 55-64) |
| `signals_vetoed` | `int` | Plays vetoed by risk officer or kill switch |
| `drought_flag` | `bool` | `true` if no actionable plays found this cycle |
| `positions_opened` | `array[object]` | Positions opened this cycle, each with `{ticker, direction, signal_id}` |
| `positions_closed` | `array[object]` | Positions closed this cycle, each with `{ticker, direction, signal_id, exit_reason}` |

#### Timing Section

| Field | Type | Description |
|---|---|---|
| `scan_start_utc` | `string (ISO-8601)` | UTC timestamp when scan cycle began |
| `scan_end_utc` | `string (ISO-8601)` | UTC timestamp when scan cycle completed |
| `duration_ms` | `int` | Total scan cycle duration in milliseconds |

#### Quality Section

| Field | Type | Description |
|---|---|---|
| `data_freshness_pct` | `float (0-100)` | Percentage of data fields classified as FRESH per Provenance Spec Section 5.1 |
| `provider_fallback_count` | `int` | Number of fields that required fallback to a secondary provider |
| `staleness_rejections` | `int` | Number of data fields rejected due to staleness (age > 2x TTL) |

### 2.3 Manifest Schema (JSON)

```json
{
  "run_id": "string (UUID v4)",
  "timestamp_utc": "string (ISO-8601)",
  "git_hash": "string",
  "config_hash": "string (sha256:...)",
  "universe_hash": "string (sha256:...)",
  "engine_version": "string (semver)",
  "python_version": "string",
  "container_image_tag": "string",

  "input": {
    "data_providers_used": [
      {
        "provider": "string",
        "status": "string (OK | DEGRADED | DOWN | RATE_LIMITED)",
        "latency_ms": "int",
        "fields_fetched": "int",
        "fallback_used": "bool"
      }
    ],
    "tickers_scanned": ["string"],
    "regime_at_scan": "string",
    "market_phase": "string",
    "data_coverage_pct": "float"
  },

  "output": {
    "plays_generated": "int",
    "signals_strict": "int",
    "signals_fallback": "int",
    "signals_vetoed": "int",
    "drought_flag": "bool",
    "positions_opened": [
      {
        "ticker": "string",
        "direction": "string (LONG | SHORT)",
        "signal_id": "string"
      }
    ],
    "positions_closed": [
      {
        "ticker": "string",
        "direction": "string (LONG | SHORT)",
        "signal_id": "string",
        "exit_reason": "string"
      }
    ]
  },

  "timing": {
    "scan_start_utc": "string (ISO-8601)",
    "scan_end_utc": "string (ISO-8601)",
    "duration_ms": "int"
  },

  "quality": {
    "data_freshness_pct": "float",
    "provider_fallback_count": "int",
    "staleness_rejections": "int"
  },

  "artifact_hashes": {
    "plays.json": "string (sha256:...)",
    "system_state.json": "string (sha256:...)",
    "scan_health.json": "string (sha256:...)",
    "drought.json": "string (sha256:...)"
  },

  "meta": {
    "manifest_version": "1.0",
    "manifest_hash": "string (sha256:... of this manifest excluding this field)"
  }
}
```

### 2.4 Manifest Integrity Rule

The `manifest_hash` field contains the SHA-256 hash of the entire manifest JSON with the `manifest_hash` field set to an empty string. This enables self-verification: any consumer can recompute the hash and detect tampering.

```python
def verify_manifest_integrity(manifest: dict) -> bool:
    stored_hash = manifest["meta"]["manifest_hash"]
    manifest_copy = copy.deepcopy(manifest)
    manifest_copy["meta"]["manifest_hash"] = ""
    computed_hash = sha256(json.dumps(manifest_copy, sort_keys=True).encode()).hexdigest()
    return f"sha256:{computed_hash}" == stored_hash
```

---

## 3. EVIDENCE CHAIN FOR SIGNALS

Every signal generated by the NZT-48 system must carry a complete evidence chain linking it to the scan cycle that produced it, the data that informed it, the strategies that scored it, and the risk officer that approved or vetoed it.

### 3.1 Signal Evidence Chain

The chain of custody for a signal follows this path:

```
run_id
  --> manifest_{run_id}.json       (scan cycle receipt)
    --> plays.json                 (all plays generated this cycle)
      --> strategies.json          (strategy-level scores and weights)
        --> intel.json             (market intelligence inputs)
          --> risk_officer.json    (risk officer approval/veto decision)
            --> provenance records (per-field data source and quality)
```

Every link in this chain must be present and verifiable. A missing link constitutes an evidence gap and triggers an integrity alert.

### 3.2 Signal Evidence Pack

Every signal with a composite score >= 55 (the fallback threshold) must have a complete signal evidence pack. Signals below 55 are logged but do not require a full evidence pack.

| Field | Type | Description |
|---|---|---|
| `signal_id` | `string` | Unique signal identifier (format: `sig_{YYYYMMDD}_{TICKER}_{STRATEGY}_{SEQ}`) |
| `run_id` | `string (UUID v4)` | The scan cycle that generated this signal |
| `ticker` | `string` | Instrument ticker symbol |
| `direction` | `string` | `LONG` or `SHORT` |
| `entry` | `float` | Proposed entry price |
| `stop` | `float` | Stop-loss price |
| `target` | `float` | Take-profit target price |
| `composite_score` | `float (0-100)` | Final composite score after all adjustments |
| `strategy_weights` | `object` | Per-strategy contribution to composite score (e.g., `{"S15": 0.72, "S3": 0.0}`) |
| `regime_at_signal` | `string` | Regime classification at the moment of signal generation |
| `data_vintage` | `string (ISO-8601)` | Timestamp of the oldest data point used in computing this signal |
| `risk_officer_decision` | `string` | `APPROVED`, `VETOED`, or `CONDITIONAL` |
| `risk_officer_reason` | `string` | Human-readable reason for the risk officer decision |
| `provenance_chain` | `array[object]` | Complete provenance records for all data fields used (per Provenance Spec Section 7.1) |

### 3.3 Signal Evidence Pack Schema (JSON)

```json
{
  "signal_id": "sig_20260227_QQQ3L_S15_001",
  "run_id": "a3f7c291-4e12-4b8a-9d5f-1c2e3f4a5b6c",
  "ticker": "QQQ3.L",
  "direction": "LONG",
  "entry": 87.45,
  "stop": 85.12,
  "target": 89.20,
  "composite_score": 72.3,
  "strategy_weights": {
    "S15_daily_target": 0.72,
    "S3_mean_reversion": 0.0
  },
  "regime_at_signal": "EXPANSION",
  "data_vintage": "2026-02-26T22:00:00Z",
  "risk_officer_decision": "APPROVED",
  "risk_officer_reason": "All risk checks passed. Position within daily capital limit. No correlated exposure.",
  "provenance_chain": [
    {
      "provider": "yfinance",
      "field": "close",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 90,
      "quality": 0.98,
      "freshness": "FRESH",
      "fallback_used": false
    }
  ]
}
```

### 3.4 Evidence Storage

Signal evidence packs are stored in two locations:

1. **Inline in artifacts**: `artifacts/signals/signal_{signal_id}.json` -- one file per signal per scan cycle.
2. **Database**: `provenance_chain` JSONB column in the `signals` table (per Provenance Spec Section 7.1).

Both locations must contain identical data. The artifact file is the authoritative source; the database record is a queryable index.

---

## 4. EVIDENCE CHAIN FOR TRADES

Every trade executed by the NZT-48 system must be traceable to its originating signal and carry a complete record from entry to exit.

### 4.1 Trade Evidence Chain

```
signal_id
  --> position_id                  (position opened from this signal)
    --> entry_fill                 (broker fill confirmation)
      --> exit_fill                (broker fill confirmation)
        --> P&L                    (realised profit/loss)
```

### 4.2 Trade Evidence Pack

Every trade, regardless of outcome, must have a complete trade evidence pack.

| Field | Type | Description |
|---|---|---|
| `trade_id` | `string` | Unique trade identifier (format: `trade_{YYYYMMDD}_{TICKER}_{SEQ}`) |
| `signal_id` | `string` | The signal that initiated this trade |
| `entry_time` | `string (ISO-8601)` | UTC timestamp of entry fill |
| `entry_price` | `float` | Actual fill price at entry |
| `exit_time` | `string (ISO-8601)` | UTC timestamp of exit fill |
| `exit_price` | `float` | Actual fill price at exit |
| `exit_reason` | `string` | One of: `STOP`, `TARGET`, `TIME_DECAY`, `KILL_SWITCH`, `MANUAL`, `SESSION_CLOSE`, `SYSTEM_HALT` |
| `pnl_pct` | `float` | Realised P&L as a percentage of entry price |
| `pnl_gbp` | `float` | Realised P&L in GBP |
| `holding_time_seconds` | `int` | Duration from entry fill to exit fill in seconds |
| `slippage_entry_bps` | `float` | Entry slippage in basis points (actual fill vs. signal entry price) |
| `slippage_exit_bps` | `float` | Exit slippage in basis points (actual fill vs. intended exit price) |

### 4.3 Trade Evidence Pack Schema (JSON)

```json
{
  "trade_id": "trade_20260227_QQQ3L_001",
  "signal_id": "sig_20260227_QQQ3L_S15_001",
  "entry_time": "2026-02-27T14:32:45Z",
  "entry_price": 87.48,
  "exit_time": "2026-02-27T15:14:22Z",
  "exit_price": 89.20,
  "exit_reason": "TARGET",
  "pnl_pct": 1.97,
  "pnl_gbp": 19.66,
  "holding_time_seconds": 2497,
  "slippage_entry_bps": 3.4,
  "slippage_exit_bps": 0.0
}
```

### 4.4 Trade Lifecycle States

Every trade transitions through the following states. Each transition is logged with a timestamp.

```
SIGNAL_GENERATED --> POSITION_OPENED --> MONITORING --> EXIT_TRIGGERED --> POSITION_CLOSED --> P&L_RECORDED
```

| State | Transition Trigger | Evidence Written |
|---|---|---|
| `SIGNAL_GENERATED` | Composite score >= threshold | Signal evidence pack |
| `POSITION_OPENED` | Entry order filled by broker | Entry fill record |
| `MONITORING` | Position is live | Periodic position snapshots (every scan cycle) |
| `EXIT_TRIGGERED` | Stop/target/time/kill/manual hit | Exit trigger record with reason |
| `POSITION_CLOSED` | Exit order filled by broker | Exit fill record |
| `P&L_RECORDED` | Trade complete | Trade evidence pack (final) |

### 4.5 Orphan Detection

A trade without a traceable signal, or a signal without a traceable run manifest, is an orphan. Orphans are forbidden.

```python
def detect_orphan_trades() -> list[str]:
    orphans = []
    for trade in get_all_trades():
        signal = get_signal(trade.signal_id)
        if signal is None:
            orphans.append(f"ORPHAN_TRADE: {trade.trade_id} has no signal {trade.signal_id}")
            continue
        manifest = get_manifest(signal.run_id)
        if manifest is None:
            orphans.append(f"ORPHAN_SIGNAL: {signal.signal_id} has no manifest {signal.run_id}")
    return orphans
```

Orphan detection runs daily at 22:00 UTC. Any orphan triggers an integrity alert.

---

## 5. HASH VERIFICATION

All artifacts are immutable once written. Immutability is enforced through cryptographic hash verification.

### 5.1 Hash Algorithm

All hashes use SHA-256. Hash strings are prefixed with `sha256:` for unambiguous identification.

### 5.2 Artifact Hashing

Every artifact file written by the engine includes a `_hash` field in its metadata:

```json
{
  "run_id": "...",
  "timestamp": "...",
  "_hash": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
}
```

The `_hash` is computed over the entire JSON content with the `_hash` field set to an empty string, using deterministic JSON serialisation (`json.dumps(obj, sort_keys=True, separators=(',', ':'))`).

### 5.3 Manifest Hash Registry

Each run manifest contains an `artifact_hashes` section listing the SHA-256 hash of every artifact file produced in that scan cycle:

```json
{
  "artifact_hashes": {
    "plays.json": "sha256:abcdef...",
    "system_state.json": "sha256:123456...",
    "scan_health.json": "sha256:789abc...",
    "drought.json": "sha256:def012..."
  }
}
```

This creates a Merkle-like structure: the manifest hash covers the artifact hashes, which cover the artifact contents.

### 5.4 Verification Script

A verification script compares stored hashes against computed hashes for any given run:

```python
def verify_run_integrity(run_id: str) -> dict:
    """
    Verify all artifact hashes for a given run_id.
    Returns: {
        "run_id": str,
        "manifest_valid": bool,
        "artifact_results": {filename: {"stored": str, "computed": str, "match": bool}},
        "tampering_detected": bool
    }
    """
    manifest_path = f"artifacts/manifests/manifest_{run_id}.json"
    manifest = json.load(open(manifest_path))

    # Verify manifest self-hash
    manifest_valid = verify_manifest_integrity(manifest)

    # Verify each referenced artifact
    artifact_results = {}
    for filename, stored_hash in manifest["artifact_hashes"].items():
        filepath = f"artifacts/{filename}"
        if not os.path.exists(filepath):
            artifact_results[filename] = {
                "stored": stored_hash,
                "computed": "FILE_MISSING",
                "match": False
            }
            continue

        content = json.load(open(filepath))
        computed_hash = compute_artifact_hash(content)
        artifact_results[filename] = {
            "stored": stored_hash,
            "computed": computed_hash,
            "match": stored_hash == computed_hash
        }

    tampering_detected = not manifest_valid or any(
        not r["match"] for r in artifact_results.values()
    )

    return {
        "run_id": run_id,
        "manifest_valid": manifest_valid,
        "artifact_results": artifact_results,
        "tampering_detected": tampering_detected
    }
```

### 5.5 Tampering Response

When a hash mismatch is detected:

1. **ALERT**: Emit `[SYSTEM] HASH_MISMATCH` alert via Telegram with affected `run_id` and filenames.
2. **QUARANTINE**: Move the affected artifacts to `artifacts/quarantine/{run_id}/` for investigation.
3. **BLOCK**: Any consumer attempting to read quarantined artifacts receives an error, not stale data.
4. **INVESTIGATE**: Operator must determine root cause (disk corruption, concurrent write, intentional modification) before clearing the quarantine.
5. **LOG**: Hash mismatch events are logged to `logs/hash_verification.log` with full details and are never automatically purged.

---

## 6. DATA PROVENANCE CHAIN

Every price, indicator, and derived value used in signal generation must be traceable to its data source. This section specifies how provenance chains integrate with the evidence framework.

### 6.1 Per-Field Provenance

Every data field used in a signal links to:

| Attribute | Description |
|---|---|
| `provider` | Canonical provider ID (e.g., `yfinance`, `cboe`, `polygon`) per Provenance Spec Section 3 |
| `fetch_timestamp` | UTC timestamp when the data was fetched from the provider |
| `raw_value` | The value as returned by the provider, before any transformation |
| `processed_value` | The value after transformation (e.g., ATR computed from OHLC) |
| `quality_score` | Normalised quality score (0.0-1.0) per Provenance Spec Section 2 |
| `ttl_seconds` | Time-to-live as defined in the Field TTL Matrix (Provenance Spec Section 4) |
| `freshness` | `FRESH`, `STALE`, or `REJECTED` per Provenance Spec Section 5.1 |

### 6.2 Provider Fallback Chain Logging

When a primary provider fails and a fallback is used, the full fallback chain must be logged:

```json
{
  "field": "close",
  "ticker": "QQQ3.L",
  "primary_provider": "yfinance",
  "primary_failure": {
    "error": "HTTP 500",
    "attempts": 3,
    "last_attempt_at": "2026-02-27T14:31:40Z"
  },
  "fallback_provider": "twelve_data",
  "fallback_latency_ms": 245,
  "fallback_quality": 0.70,
  "chain_position": 2,
  "total_chain_length": 3,
  "note": "yfinance failed; twelve_data used as first fallback"
}
```

Fallback events are aggregated per scan cycle in the manifest's `quality.provider_fallback_count` field and detailed in `artifacts/fallback_log/{run_id}.json`.

### 6.3 Data Licensing Compliance

Provider usage must comply with data licensing terms. The system tracks and enforces the following:

| Provider | Licence Type | Commercial Use | Redistribution | Compliance Note |
|---|---|---|---|---|
| yfinance | Free / Non-commercial | Not permitted for commercial redistribution | Prohibited | Use as fallback only in production; primary for paper trading |
| Polygon | Commercial ($29/mo) | Permitted | Per agreement | Primary candidate for production migration (REQ-020) |
| Alpha Vantage | Free tier / Premium | Limited (free), Permitted (premium) | Per agreement | Indicator computation, earnings calendar |
| Twelve Data | Free tier / Premium | Limited (free), Permitted (premium) | Per agreement | Fallback for price data |
| FMP | Free tier / Premium | Limited (free), Permitted (premium) | Per agreement | Fundamentals, financial statements |
| Finnhub | Free tier / Premium | Limited (free), Permitted (premium) | Per agreement | News, earnings, SEC filings |
| CBOE | Public feeds | Permitted (public data) | Per terms | VIX, term structure, put/call ratios |
| Squeezemetrics | Public | Permitted (public data) | Per terms | DIX, GEX (unique, no fallback) |

The system logs which provider served each field. Any compliance audit can reconstruct exactly which providers were used for which purposes across any time period.

---

## 7. REPLAY RULES

Deterministic replay is the ability to re-run a historical scan cycle using cached inputs and produce identical outputs. This capability is essential for debugging, backtesting code changes, and auditing past decisions.

### 7.1 Deterministic Replay Requirement

Given a `run_id`, the system must be able to reproduce the exact output of that scan cycle, subject to floating-point tolerance.

### 7.2 Replay Prerequisites

For a scan cycle to be replayable, the following snapshots must exist:

| Snapshot | Content | Storage Location | Retention |
|---|---|---|---|
| Market data snapshot | All raw price/indicator data fetched during the cycle | `artifacts/replay_cache/{run_id}/market_data.json` | 90 days online, 1 year archive |
| Config snapshot | Complete `settings.yaml` at cycle start | Git history (tracked by `config_hash` in manifest) | Indefinite (git) |
| Universe snapshot | Active ticker list at cycle start | Git history (tracked by `universe_hash` in manifest) | Indefinite (git) |
| Regime state snapshot | Regime classification and supporting indicators | `artifacts/replay_cache/{run_id}/regime_state.json` | 90 days online, 1 year archive |
| Provider response cache | Raw HTTP responses from each provider | `artifacts/replay_cache/{run_id}/provider_responses/` | 90 days online |

### 7.3 Replay Mode Activation

Replay mode is activated by setting the `DETERMINISTIC` environment variable:

```bash
DETERMINISTIC=true RUN_ID=a3f7c291-4e12-4b8a-9d5f-1c2e3f4a5b6c python main.py --replay
```

When `DETERMINISTIC=true`:

1. The engine reads all market data from `artifacts/replay_cache/{run_id}/` instead of live provider feeds.
2. The engine loads the config snapshot matching the manifest's `config_hash`.
3. The engine loads the universe snapshot matching the manifest's `universe_hash`.
4. The engine seeds any random number generators with a fixed seed derived from `run_id`.
5. No Telegram messages are sent.
6. No PDF reports are generated.
7. No positions are opened or closed.
8. Output is written to `artifacts/replay_output/{run_id}/` for comparison.

### 7.4 Replay Verification

After replay, the system compares replay output against original artifacts:

```python
def verify_replay(run_id: str) -> dict:
    """
    Compare replay output against original artifacts.
    Returns comparison report with field-level diffs.
    """
    original_plays = load_json(f"artifacts/plays.json", run_id=run_id)
    replay_plays = load_json(f"artifacts/replay_output/{run_id}/plays.json")

    FLOAT_TOLERANCE = 1e-6
    mismatches = []

    for orig, replay in zip(original_plays["plays"], replay_plays["plays"]):
        for field in ["ticker", "direction", "strategy"]:
            if orig[field] != replay[field]:
                mismatches.append({
                    "field": field,
                    "original": orig[field],
                    "replay": replay[field],
                    "type": "EXACT_MISMATCH"
                })
        for field in ["entry", "stop", "target", "score"]:
            if abs(orig[field] - replay[field]) > FLOAT_TOLERANCE:
                mismatches.append({
                    "field": field,
                    "original": orig[field],
                    "replay": replay[field],
                    "delta": abs(orig[field] - replay[field]),
                    "type": "FLOAT_MISMATCH"
                })

    return {
        "run_id": run_id,
        "plays_original": len(original_plays["plays"]),
        "plays_replay": len(replay_plays["plays"]),
        "mismatches": mismatches,
        "verdict": "PASS" if len(mismatches) == 0 else "FAIL"
    }
```

### 7.5 Tolerance

- **String fields** (ticker, direction, strategy, regime): exact match required.
- **Float fields** (entry, stop, target, score, confidence): match within `1e-6` absolute tolerance.
- **Integer fields** (plays_generated, signals_strict): exact match required.
- **Timestamp fields**: match within 1 second tolerance (to accommodate system clock differences).

### 7.6 Non-Determinism Sources

The following are known sources of non-determinism that must be controlled during replay:

| Source | Mitigation |
|---|---|
| System clock (`datetime.now()`) | Replay injects the original `scan_start_utc` from the manifest |
| Random number generation | Seed with deterministic hash of `run_id` |
| Provider response order | Replay serves cached responses in original order |
| Thread scheduling | Replay runs single-threaded |
| Floating-point accumulation order | Replay uses same sort order as original (enforced by deterministic `sort_keys=True`) |

---

## 8. RETENTION POLICY

All evidence artifacts are subject to the following retention schedule. Retention is enforced by an automated archival job running daily at 02:00 UTC.

### 8.1 Retention Schedule

| Artifact Category | Online Retention | Archive Retention | Archive Destination | Deletion Policy |
|---|---|---|---|---|
| Scan cycle artifacts (`artifacts/*.json`) | 90 days | 1 year | S3 bucket or local backup (`/backup/nzt48/artifacts/`) | Purge archive after 1 year |
| Run manifests (`artifacts/manifests/`) | 1 year | Indefinite | S3 bucket or local backup | Never deleted |
| Trade records (database + JSON) | Indefinite | N/A | N/A | **Never deleted** |
| Signal evidence packs | 1 year | Indefinite | S3 bucket or local backup | Never deleted |
| Config snapshots (`settings.yaml`) | Indefinite | N/A | Git history | Never deleted |
| PDF reports | 90 days online | 1 year archive | S3 bucket or local backup | Purge archive after 1 year |
| Telegram logs | 30 days online | 90 days archive | Compressed JSON (`data/audit/telegram_YYYYWW.json.gz`) | Purge archive after 90 days |
| Replay cache | 90 days | N/A | N/A | Purge after 90 days |
| Hash verification logs | Indefinite | N/A | N/A | **Never deleted** |
| Integrity alerts | 1 year | Indefinite | S3 bucket or local backup | Never deleted |

### 8.2 Archival Process

```
DAILY AT 02:00 UTC:

1. SCAN online storage for artifacts older than retention threshold.
2. COMPRESS artifacts into dated archive bundles:
   - artifacts_YYYYMMDD.tar.gz (scan cycle artifacts)
   - manifests_YYYYMM.tar.gz (monthly manifest bundles)
   - signals_YYYYMM.tar.gz (monthly signal evidence packs)
3. VERIFY archive integrity (compute SHA-256 of archive, store in archive_manifest.json).
4. UPLOAD to archive destination (S3 or local backup).
5. VERIFY upload success (compare local hash vs remote hash).
6. DELETE online copies ONLY after successful archive verification.
7. LOG archival actions to logs/archival.log.
```

### 8.3 Immutability Guarantee

Once an artifact enters the archive, it is immutable. The archive manifest (`archive_manifest.json`) records the SHA-256 hash of every archived file. Any modification to an archived file constitutes a forensic incident.

---

## 9. EVIDENCE INDEX REQUIREMENTS

A master evidence index provides a single point of reference for all evidence artifacts, linking claims in governance documents to the proof that supports them.

### 9.1 Index Location

```
annexes/EVIDENCE_INDEX.md
```

### 9.2 Index Format

The evidence index is a Markdown table with the following columns:

| Column | Description |
|---|---|
| `evidence_id` | Unique identifier (format: `EV-{SEQ:04d}`) |
| `description` | Brief description of what the evidence demonstrates |
| `path` | Relative path to the evidence artifact |
| `date` | Date the evidence was captured or generated (YYYY-MM-DD) |
| `used_in_sections` | Comma-separated list of document sections that reference this evidence |

### 9.3 Index Example

```markdown
| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-0001 | Run manifest for scan cycle a3f7c291 | artifacts/manifests/manifest_a3f7c291.json | 2026-02-27 | ERS-001 S2, ASP-001 S3 |
| EV-0002 | Signal evidence pack for QQQ3.L S15 signal | artifacts/signals/signal_sig_20260227_QQQ3L_S15_001.json | 2026-02-27 | ERS-001 S3 |
| EV-0003 | Hash verification report for run a3f7c291 | artifacts/verification/verify_a3f7c291.json | 2026-02-27 | ERS-001 S5 |
| EV-0004 | Replay verification for run a3f7c291 | artifacts/replay_output/a3f7c291/replay_report.json | 2026-02-27 | ERS-001 S7 |
```

### 9.4 Automated Index Maintenance

An evidence collection script updates the index automatically:

```python
def update_evidence_index():
    """
    Scan artifacts/ for evidence files.
    Update annexes/EVIDENCE_INDEX.md with new entries.
    Preserve existing entries (append-only, never delete).
    """
    existing = parse_evidence_index("annexes/EVIDENCE_INDEX.md")
    new_evidence = scan_for_new_evidence("artifacts/")

    for item in new_evidence:
        if item.path not in {e.path for e in existing}:
            existing.append(EvidenceEntry(
                evidence_id=next_evidence_id(existing),
                description=item.description,
                path=item.path,
                date=item.date,
                used_in_sections=item.sections
            ))

    write_evidence_index("annexes/EVIDENCE_INDEX.md", existing)
```

The script runs daily at 03:00 UTC (after the 02:00 UTC archival job completes).

### 9.5 Cross-Reference Requirement

The main binder document (IC/PM Approval Pack) must reference the evidence index for every factual claim. Claims without evidence index entries are flagged during document review.

---

## 10. ACCEPTANCE TESTS

### ERS-T01: Run Manifest Completeness

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T01 |
| **Objective** | Verify that a run manifest is generated for every scan cycle with all required fields present |
| **Precondition** | Engine running in paper mode. At least one scan cycle completed. |
| **Procedure** | 1. Trigger a scan cycle. 2. Wait for cycle completion. 3. Load the manifest file from `artifacts/manifests/`. 4. Validate all required fields from Section 2.2 are present and non-null. 5. Validate field types match schema. 6. Validate `run_id` in manifest matches `run_id` in all other artifacts from this cycle. |
| **Pass Criteria** | All required fields present. All types valid. `run_id` consistent across manifest and artifacts. `manifest_hash` self-verification passes. |
| **Fail Criteria** | Any required field missing or null. Type mismatch. `run_id` inconsistency. `manifest_hash` verification fails. |

### ERS-T02: Signal Evidence Pack Completeness

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T02 |
| **Objective** | Verify that every signal with composite score >= 55 has a complete evidence pack |
| **Precondition** | Engine running. At least one signal generated with composite >= 55. |
| **Procedure** | 1. Trigger scan cycles until a signal with composite >= 55 is generated. 2. Locate the signal evidence pack in `artifacts/signals/`. 3. Validate all fields from Section 3.2 are present. 4. Validate `provenance_chain` contains at least one entry. 5. Validate `run_id` in signal pack matches an existing manifest. 6. Validate `data_vintage` is not in the future. |
| **Pass Criteria** | All fields present. Provenance chain non-empty. `run_id` links to valid manifest. `data_vintage` is in the past. |
| **Fail Criteria** | Any required field missing. Empty provenance chain. Orphan signal (no matching manifest). Future `data_vintage`. |

### ERS-T03: Hash Verification

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T03 |
| **Objective** | Verify that hash verification passes for all artifacts in the latest scan cycle |
| **Precondition** | At least one scan cycle completed with artifacts written. |
| **Procedure** | 1. Identify the latest `run_id` from `artifacts/manifests/`. 2. Run `verify_run_integrity(run_id)`. 3. Check manifest self-hash. 4. Check all artifact hashes against manifest's `artifact_hashes` section. |
| **Pass Criteria** | `manifest_valid` is `true`. All artifact hashes match. `tampering_detected` is `false`. |
| **Fail Criteria** | Manifest self-hash fails. Any artifact hash mismatch. `tampering_detected` is `true`. |

### ERS-T04: Deterministic Replay

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T04 |
| **Objective** | Verify that replaying yesterday's scan produces identical output within tolerance |
| **Precondition** | Yesterday's scan cycle completed. Replay cache exists for that `run_id`. |
| **Procedure** | 1. Select a `run_id` from yesterday's manifests. 2. Activate replay mode: `DETERMINISTIC=true RUN_ID={run_id} python main.py --replay`. 3. Wait for replay completion. 4. Run `verify_replay(run_id)`. 5. Check all fields match within tolerance (Section 7.5). |
| **Pass Criteria** | `verdict` is `PASS`. Zero mismatches. Play count matches. |
| **Fail Criteria** | Any field mismatch beyond tolerance. Play count differs. Replay crashes or hangs. |

### ERS-T05: Evidence Index Coverage

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T05 |
| **Objective** | Verify that the evidence index covers all claims in the IC/PM Approval Pack |
| **Precondition** | IC/PM Approval Pack document exists. Evidence index has been generated. |
| **Procedure** | 1. Parse the IC/PM Approval Pack for all factual claims (statements prefixed with "Evidence:" or "Proof:"). 2. For each claim, verify it references an `evidence_id` from the evidence index. 3. For each referenced `evidence_id`, verify the artifact at the indexed path exists and is readable. |
| **Pass Criteria** | Every factual claim has a corresponding evidence index entry. Every referenced artifact exists. |
| **Fail Criteria** | Any claim without evidence index reference. Any referenced artifact missing or unreadable. |

### ERS-T06: Retention Policy Enforcement

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T06 |
| **Objective** | Verify that no artifacts older than their retention threshold remain in online storage |
| **Precondition** | System has been running for > 90 days (or inject backdated test artifacts). |
| **Procedure** | 1. Inject test artifacts with timestamps > 90 days old into `artifacts/`. 2. Run the archival job manually. 3. Verify injected artifacts have been moved to archive. 4. Verify archive contains the artifacts with correct hashes. 5. Verify online `artifacts/` directory contains no files older than 90 days. 6. Verify trade records and manifests are NOT archived (indefinite retention). |
| **Pass Criteria** | Old scan artifacts archived. Archive hashes valid. Trade records and manifests preserved online. No retention-expired files in online storage. |
| **Fail Criteria** | Old artifacts remain in online storage. Archive corrupted. Trade records accidentally archived. |

### ERS-T07: Provider Fallback Chain Logging

| Aspect | Detail |
|---|---|
| **Test ID** | ERS-T07 |
| **Objective** | Verify that every data fetch logs the provider fallback chain, including when fallback is not used |
| **Precondition** | Engine running. Data providers accessible. |
| **Procedure** | 1. Run a scan cycle with all providers healthy. Verify provenance records show `fallback_used: false` for all fields. 2. Disable the primary provider (yfinance) by mocking HTTP 500. 3. Run a scan cycle. 4. Verify provenance records show `fallback_used: true` for affected fields. 5. Verify `artifacts/fallback_log/{run_id}.json` contains the fallback chain details. 6. Verify the manifest's `quality.provider_fallback_count` is > 0. |
| **Pass Criteria** | Normal cycle: all `fallback_used: false`. Degraded cycle: affected fields show `fallback_used: true` with correct fallback provider. Fallback log exists with chain details. Manifest quality section accurate. |
| **Fail Criteria** | Missing provenance records. Fallback not logged. Manifest quality section inaccurate. Silent fallback (no log entry). |

### Acceptance Test Summary

| Test ID | Category | Key Assertion |
|---|---|---|
| ERS-T01 | Run Manifest | Every scan cycle produces a complete, self-verifiable manifest |
| ERS-T02 | Signal Evidence | Every qualifying signal has a traceable evidence pack |
| ERS-T03 | Hash Integrity | All artifact hashes match and no tampering detected |
| ERS-T04 | Reproducibility | Historical scans replay deterministically within tolerance |
| ERS-T05 | Evidence Coverage | All governance claims are backed by indexed evidence |
| ERS-T06 | Retention | Retention policy enforced; nothing expires prematurely or lingers |
| ERS-T07 | Provenance | Provider fallback chains logged for every data fetch |

---

## 11. REVISION HISTORY

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-02-27 | NZT-48 System | Initial specification. Run manifests, signal evidence chains, trade evidence chains, hash verification, data provenance integration, deterministic replay, retention policy, evidence index, and 7 acceptance tests defined. |

---

**END OF DOCUMENT NZT48-ANNEX-ERS-001**

This specification is binding. Every claim must be backed by evidence. Every signal must carry a complete evidence pack. Every scan cycle must produce a verifiable manifest. Every artifact must be hash-verified. Every historical scan must be replayable. No exceptions.
