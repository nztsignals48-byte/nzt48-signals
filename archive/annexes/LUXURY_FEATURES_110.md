# NZT-48 Luxury Features (110/100)

| Field       | Value                  |
|-------------|------------------------|
| Document ID | NZT48-ANNEX-LUX-001   |
| Version     | 1.0                    |
| Date        | 2026-02-27             |
| Status      | PLANNED                |
| Author      | NZT-48 Engineering     |
| Scope       | Post-baseline enhancements for operational excellence |

---

## 1. Purpose

The NZT-48 baseline delivers a fully wired, integrity-checked, leveraged ISA intraday trading system targeting 2% daily compounding. The baseline is the **100/100** --- every contract honoured, every gate enforced, every artifact auditable.

This document specifies the **110/100 luxury features** --- capabilities that elevate the system from *correct* to *flawless*. These are not nice-to-haves; they are the difference between a system that works and a system that a portfolio manager trusts with real capital. Each feature reduces operational risk, accelerates incident resolution, or provides visibility that prevents losses before they occur.

---

## 2. Feature Catalogue

### 2.1 LUX-001: One-Click Evidence Pack Export

**Priority:** P1 (implement with W13)

#### 2.1.1 Overview

A single button in the War Room dashboard that packages the complete daily evidence trail into a portable, self-contained ZIP archive. When a PM asks "show me everything the system did today," the answer is one click away.

#### 2.1.2 Specification

| Attribute         | Value |
|-------------------|-------|
| Trigger           | War Room button: **"Export Today's Evidence Pack"** |
| Output            | `evidence_pack_YYYYMMDD.zip` |
| Target directory  | `data/evidence_packs/` |
| Max age retained  | 90 days (auto-pruned) |

#### 2.1.3 ZIP Contents

```
evidence_pack_20260227.zip
  artifacts/
    *.json                        # All artifact files for the date
  data/reports/
    *.pdf                         # All PDFs generated for the date
  telegram/
    telegram_debug.jsonl          # Last 24h of Telegram dispatch log
  gates/
    readiness_gate.json           # Go-Live Gate status snapshot
    integrity_status.json         # Wiring integrity check result
  manifests/
    manifest_YYYYMMDD_*.json      # All manifests for the date
  screenshots/                    # Playwright screenshots (if run exists)
    *.png
  META.json                       # Pack metadata (see schema below)
```

#### 2.1.4 META.json Schema

```json
{
  "pack_id": "EP-20260227-001",
  "generated_at": "2026-02-27T18:30:00Z",
  "date_covered": "2026-02-27",
  "engine_version": "2.0.0",
  "file_count": 47,
  "total_size_bytes": 2415632,
  "integrity_hash": "sha256:abcdef1234567890...",
  "generator": "evidence_pack_exporter v1.0"
}
```

#### 2.1.5 Implementation Plan

| Step | Description | File |
|------|-------------|------|
| 1 | Create exporter module | `core/evidence_pack.py` |
| 2 | Implement file collector (walks artifact/report/manifest dirs, filters by date) | `core/evidence_pack.py` |
| 3 | Implement ZIP builder with SHA-256 integrity hash | `core/evidence_pack.py` |
| 4 | Add auto-prune for packs older than 90 days | `core/evidence_pack.py` |
| 5 | Wire War Room button to FastAPI endpoint | `api/routes/evidence.py` |
| 6 | War Room UI: button + download handler | Dashboard component |

#### 2.1.6 API Endpoint

```
POST /api/evidence-pack/export
  Query params: date=YYYY-MM-DD (default: today)
  Response: 200 OK, Content-Type: application/zip
  Error: 404 if no artifacts exist for date
```

---

### 2.2 LUX-002: Deterministic Replay Mode

**Priority:** P3 (implement after PAPER READY)

#### 2.2.1 Overview

Re-run any historical trading day exactly as it occurred, using stored artifacts and historical price data, to see what signals the *current* codebase would have generated. This is the system's time machine --- essential for debugging, backtesting code changes, and answering the question: "what if we had this gate yesterday?"

#### 2.2.2 Specification

| Attribute           | Value |
|---------------------|-------|
| Input               | Target date + optional code branch/patch |
| Data sources        | `manifests/` artifacts for target date + `research/` historical price DB |
| Output              | Replay report comparing actual vs. replayed signals |
| Isolation guarantee | **No Telegram dispatch. No live PDF generation. No state mutation.** |

#### 2.2.3 Replay Engine Architecture

```
                  +-------------------+
                  |  Replay Request   |
                  |  (date, config)   |
                  +--------+----------+
                           |
                  +--------v----------+
                  |  Manifest Loader  |
                  |  Reads stored     |
                  |  artifacts for    |
                  |  target date      |
                  +--------+----------+
                           |
                  +--------v----------+
                  |  Data Injector    |
                  |  Loads historical |
                  |  price data from  |
                  |  research DB      |
                  +--------+----------+
                           |
                  +--------v----------+
                  |  Engine Runner    |
                  |  (isolated)       |
                  |  Runs full scan   |
                  |  pipeline with    |
                  |  injected data    |
                  +--------+----------+
                           |
                  +--------v----------+
                  |  Diff Comparator  |
                  |  Actual signals   |
                  |  vs. replayed     |
                  |  signals          |
                  +--------+----------+
                           |
                  +--------v----------+
                  |  Replay Report    |
                  |  (JSON + human-   |
                  |   readable)       |
                  +-------------------+
```

#### 2.2.4 Replay Report Schema

```json
{
  "replay_id": "RPL-20260227-001",
  "target_date": "2026-02-25",
  "replayed_at": "2026-02-27T14:00:00Z",
  "engine_version_at_replay": "2.0.1",
  "engine_version_at_original": "2.0.0",
  "config_diff": { "changed_keys": ["strategies.S15.atr_multiplier"] },
  "signal_comparison": [
    {
      "ticker": "QQQ3.L",
      "original_score": 78.5,
      "replayed_score": 82.1,
      "original_action": "LONG",
      "replayed_action": "LONG",
      "delta": "+3.6",
      "match": true
    }
  ],
  "summary": {
    "total_tickers_scanned": 12,
    "signals_matched": 10,
    "signals_diverged": 2,
    "new_signals": 0,
    "dropped_signals": 0
  },
  "isolation_verified": true
}
```

#### 2.2.5 Implementation Plan

| Step | Description | File |
|------|-------------|------|
| 1 | Create replay engine with manifest loader | `core/replay_engine.py` |
| 2 | Build data injector (reads historical prices, overrides live feeds) | `core/replay_engine.py` |
| 3 | Implement isolated engine runner (strips Telegram + PDF hooks) | `core/replay_engine.py` |
| 4 | Build diff comparator (signal-level comparison with delta calc) | `core/replay_engine.py` |
| 5 | Create replay report generator (JSON + markdown) | `core/replay_engine.py` |
| 6 | Add CLI entry point: `python -m core.replay_engine --date 2026-02-25` | `core/replay_engine.py` |
| 7 | Wire War Room UI for on-demand replay | Dashboard component |

#### 2.2.6 Safety Constraints

- All Telegram dispatch functions are replaced with no-op stubs during replay.
- All PDF generators write to `data/replay_output/` instead of `data/reports/`.
- No portfolio state, position, or P&L records are mutated.
- Replay artifacts are written to `data/replays/RPL-{id}/` and never mixed with live artifacts.
- A replay cannot trigger another replay (no recursion).

---

### 2.3 LUX-003: Incident Library

**Priority:** P1 (implement with W13)

#### 2.3.1 Overview

Every integrity alert, wiring drift, startup failure, and manual intervention becomes a permanent, queryable incident record. The system does not just detect problems --- it remembers them, tracks their resolution, and surfaces patterns.

#### 2.3.2 Specification

| Attribute       | Value |
|-----------------|-------|
| Storage         | `data/incidents.jsonl` (append-only) |
| Retention       | Indefinite (incidents are never auto-deleted) |
| War Room panel  | **"Incident Log"** --- sortable, filterable, searchable |

#### 2.3.3 Incident Record Schema

```json
{
  "incident_id": "INC-20260227-001",
  "timestamp": "2026-02-27T08:14:32Z",
  "type": "integrity_alert | wiring_drift | startup_failure | manual_intervention | data_gap | provider_outage | gate_block | signal_anomaly",
  "severity": "critical | high | medium | low",
  "source_module": "integrity_checker",
  "description": "Wiring path scanner_tick -> score_engine returned null for QQQ3.L",
  "evidence_snapshot": {
    "artifact_refs": ["artifacts/scan_20260227_081430.json"],
    "config_hash": "sha256:abc123...",
    "stack_trace": "Traceback (most recent call last):\n  ..."
  },
  "resolution": null,
  "resolved_by": null,
  "resolved_at": null,
  "time_to_resolve_minutes": null,
  "tags": ["wiring", "QQQ3.L", "scan_tick"],
  "related_incidents": []
}
```

#### 2.3.4 Incident Types

| Type                  | Auto-created by | Severity default |
|-----------------------|-----------------|------------------|
| `integrity_alert`     | Integrity checker | high |
| `wiring_drift`        | Wiring validator | critical |
| `startup_failure`     | Boot sequence | critical |
| `manual_intervention` | Operator (manual entry) | medium |
| `data_gap`            | Data provider monitor | high |
| `provider_outage`     | Data provider monitor | high |
| `gate_block`          | Readiness gate | high |
| `signal_anomaly`      | Score validator | medium |

#### 2.3.5 War Room Panel: Incident Log

```
+--------------------------------------------------------------+
|  INCIDENT LOG                           [Filter] [Search]     |
+--------------------------------------------------------------+
| ID               | Time  | Type          | Sev  | Status     |
|------------------|-------|---------------|------|------------|
| INC-20260227-001 | 08:14 | wiring_drift  | CRIT | OPEN       |
| INC-20260226-003 | 16:42 | data_gap      | HIGH | RESOLVED   |
| INC-20260226-002 | 09:01 | gate_block    | HIGH | RESOLVED   |
| INC-20260226-001 | 07:15 | startup_fail  | CRIT | RESOLVED   |
+--------------------------------------------------------------+
| Showing 4 of 127 incidents  |  OPEN: 1  |  MTTR: 23 min     |
+--------------------------------------------------------------+
```

#### 2.3.6 Derived Metrics

| Metric | Definition |
|--------|------------|
| MTTR (Mean Time to Resolve) | Average `time_to_resolve_minutes` across resolved incidents |
| Incident Frequency | Incidents per day (7d rolling) |
| Repeat Offender Rate | % of incidents with `related_incidents` links |
| Open Incident Count | Number of incidents with `resolution == null` |

#### 2.3.7 Implementation Plan

| Step | Description | File |
|------|-------------|------|
| 1 | Create incident manager (write, query, resolve, link) | `core/incident_library.py` |
| 2 | Wire auto-creation hooks into integrity checker | `core/integrity.py` |
| 3 | Wire auto-creation hooks into wiring validator | `core/wiring.py` |
| 4 | Wire auto-creation hooks into boot sequence | `main.py` |
| 5 | Wire auto-creation hooks into data provider monitor | `core/data_providers.py` |
| 6 | Wire auto-creation hooks into readiness gate | `core/readiness_gate.py` |
| 7 | Add resolution API endpoint | `api/routes/incidents.py` |
| 8 | Build War Room incident log panel | Dashboard component |
| 9 | Implement derived metrics calculator | `core/incident_library.py` |

---

### 2.4 LUX-004: Manager One-Pager (Auto-Generated Daily)

**Priority:** P1 (implement with W13)

#### 2.4.1 Overview

A single-page PDF generated twice daily that gives a portfolio manager complete system awareness in under 60 seconds. If the PM reads nothing else, this one message tells them: Is the system healthy? What happened today? Should I be worried?

#### 2.4.2 Specification

| Attribute       | Value |
|-----------------|-------|
| Generation times | 07:00 UK (pre-market brief) and 22:00 UK (end-of-day wrap) |
| Output format   | Single-page PDF (A4 landscape) |
| Output path     | `data/reports/one_pager_YYYYMMDD_HHMM.pdf` |
| Telegram delivery | Auto-sent as `[DAILY BRIEF]` message with PDF attachment |
| Max file size   | 500 KB |

#### 2.4.3 One-Pager Layout

```
+------------------------------------------------------------------+
| NZT-48 DAILY BRIEF          2026-02-27 07:00 UK        [AM/PM]  |
+------------------------------------------------------------------+
|                                                                   |
|  SYSTEM WIRING          TOP 3 SIGNALS             RISK METRICS   |
|  +----------+           +------------------+      +------------+ |
|  | [GREEN]  |           | 1. QQQ3.L  82.1  |      | VaR: 1.2%  | |
|  | All 14   |           |    LONG  +2.0%   |      | DD:  0.3%  | |
|  | paths OK |           | 2. 3LUS.L 76.4  |      | Exp: 4.1%  | |
|  |          |           |    LONG  +2.0%   |      | Corr: 0.41 | |
|  +----------+           | 3. NVD3.L 71.8  |      +------------+ |
|                         |    HOLD         |                      |
|  DROUGHT STATUS         +------------------+      P&L (PAPER)   |
|  +----------+                                     +------------+ |
|  | Day 0    |           GO-LIVE GATE              | Today: +1.8%| |
|  | Last sig |           +------------------+      | Week:  +4.2%| |
|  | today    |           | [NOT READY]      |      | Total: +12% | |
|  +----------+           | Missing: 3 items |      | Equity: 11k | |
|                         +------------------+      +------------+ |
|                                                                   |
|  Generated by NZT-48 v2.0 | Evidence pack: EP-20260227-001      |
+------------------------------------------------------------------+
```

#### 2.4.4 Data Sources

| Section | Source |
|---------|--------|
| System Wiring | `integrity_status.json` |
| Top 3 Signals | Latest scan artifacts, sorted by score descending |
| Drought Status | `regime_drought.json` |
| Risk Metrics | Portfolio risk calculator output |
| P&L | Paper portfolio tracker |
| Go-Live Gate | `readiness_gate.json` |

#### 2.4.5 Telegram Message Format

```
[DAILY BRIEF] 2026-02-27 07:00 UK

Wiring: GREEN (14/14 paths OK)
Top Signal: QQQ3.L @ 82.1 (LONG +2.0%)
Drought: Day 0 (signal active)
P&L Today: +1.8% | Total: +12.1%
Go-Live: NOT READY (3 items remaining)

Full brief attached.
```

#### 2.4.6 Implementation Plan

| Step | Description | File |
|------|-------------|------|
| 1 | Create one-pager data aggregator | `delivery/manager_one_pager.py` |
| 2 | Build PDF renderer (single-page A4 landscape, ReportLab) | `delivery/manager_one_pager.py` |
| 3 | Implement AM/PM scheduling (07:00 and 22:00 UK) | `main.py` (APScheduler) |
| 4 | Wire Telegram delivery with `[DAILY BRIEF]` tag | `delivery/manager_one_pager.py` |
| 5 | Add to manifest (one-pager generation becomes auditable event) | `core/manifest.py` |

---

### 2.5 LUX-005: SLA Dashboard

**Priority:** P2 (implement after PAPER STABLE)

#### 2.5.1 Overview

A real-time War Room panel that tracks operational SLAs with colour-coded status indicators. The system does not just run --- it knows whether it is running *well enough*.

#### 2.5.2 SLA Definitions

| SLA ID | Metric | Target | Measurement |
|--------|--------|--------|-------------|
| SLA-001 | Scan Tick Completion | >95% of ticks complete within 45s | `(ticks_under_45s / total_ticks) * 100` |
| SLA-002 | Outcome Resolution | >90% of signals resolved within 24h | `(signals_resolved_24h / total_signals) * 100` |
| SLA-003 | PDF Audit Pass Rate | 100% of PDFs pass QA checks | `(pdfs_passed / pdfs_generated) * 100` |
| SLA-004 | Telegram Invalid Suppression | 100% of invalid-score messages blocked | `(invalid_blocked / invalid_total) * 100` |
| SLA-005 | Data Provider Uptime | >99% availability per provider | `(successful_requests / total_requests) * 100` per provider |

#### 2.5.3 Colour Coding

| Status | Condition | Visual |
|--------|-----------|--------|
| GREEN | Meeting SLA target | Solid green indicator |
| YELLOW | Within 10% of SLA breach (e.g., SLA-001 at 86-95%) | Amber pulse indicator |
| RED | SLA breached | Red flash indicator + auto-creates incident (LUX-003) |

#### 2.5.4 Dashboard Panel Layout

```
+--------------------------------------------------------------+
|  SLA DASHBOARD                        Last updated: 08:14:32 |
+--------------------------------------------------------------+
| Metric                    | Current | Target | Status | Trend |
|---------------------------|---------|--------|--------|-------|
| Scan Tick (<45s)          |  97.2%  |  >95%  | GREEN  |  -->  |
| Outcome Resolution (<24h) |  88.1%  |  >90%  | YELLOW |  v    |
| PDF Audit Pass            | 100.0%  | 100%   | GREEN  |  -->  |
| Telegram Suppression      | 100.0%  | 100%   | GREEN  |  -->  |
| Data: yfinance            |  99.8%  |  >99%  | GREEN  |  -->  |
| Data: Alpha Vantage       |  97.2%  |  >99%  | RED    |  v    |
+--------------------------------------------------------------+
| Overall SLA Health: 4/6 GREEN  |  1 YELLOW  |  1 RED         |
+--------------------------------------------------------------+
```

#### 2.5.5 SLA Breach Escalation

When any SLA enters RED status:

1. Auto-create incident in Incident Library (LUX-003) with type `sla_breach`.
2. Send Telegram alert: `[SLA BREACH] {metric_name} at {current_value}% (target: {target}%)`.
3. Log to `data/sla_history.jsonl` for trend analysis.

#### 2.5.6 SLA History Schema

```json
{
  "timestamp": "2026-02-27T08:14:32Z",
  "sla_id": "SLA-001",
  "metric_name": "scan_tick_completion",
  "current_value": 97.2,
  "target_value": 95.0,
  "status": "green",
  "window": "24h",
  "sample_size": 1440
}
```

#### 2.5.7 Implementation Plan

| Step | Description | File |
|------|-------------|------|
| 1 | Create SLA metric collector (polls each metric source) | `core/sla_monitor.py` |
| 2 | Implement threshold evaluator (green/yellow/red logic) | `core/sla_monitor.py` |
| 3 | Wire breach escalation to Incident Library (LUX-003) | `core/sla_monitor.py` |
| 4 | Wire breach alert to Telegram | `core/sla_monitor.py` |
| 5 | Create SLA history writer (`data/sla_history.jsonl`) | `core/sla_monitor.py` |
| 6 | Build War Room SLA dashboard panel | Dashboard component |
| 7 | Add SLA trend charts (7d rolling) | Dashboard component |

---

### 2.6 LUX-006: Change Impact Simulator

**Priority:** P2 (implement after PAPER STABLE)

#### 2.6.1 Overview

Before deploying any code or configuration change, run a simulation that answers: "What would this change have done to yesterday's trading day?" This is the system's staging environment --- except instead of a separate server, it runs a parallel universe using real historical data.

#### 2.6.2 Specification

| Attribute         | Value |
|-------------------|-------|
| Input             | Proposed change (config diff or code branch) + reference date |
| Baseline          | Yesterday's artifacts (or any specified date) |
| Output            | Impact report: broken paths, affected signals, regime changes |
| Isolation         | Same guarantees as Replay Mode (LUX-002): no Telegram, no live state |

#### 2.6.3 Impact Report Schema

```json
{
  "simulation_id": "SIM-20260227-001",
  "simulated_at": "2026-02-27T14:30:00Z",
  "reference_date": "2026-02-26",
  "change_description": "Increased S15 ATR multiplier from 1.0 to 1.5",
  "change_type": "config | code | both",
  "config_diff": {
    "strategies.S15.atr_stop_multiplier": { "before": 1.0, "after": 1.5 }
  },
  "impact": {
    "wiring_paths_broken": 0,
    "wiring_paths_affected": 2,
    "wiring_path_details": [
      {
        "path": "scanner_tick -> score_engine -> S15",
        "status": "OK",
        "note": "Score calculation uses ATR multiplier; outputs will differ"
      }
    ],
    "signals_affected": 4,
    "signal_details": [
      {
        "ticker": "QQQ3.L",
        "original_score": 78.5,
        "simulated_score": 72.1,
        "original_action": "LONG",
        "simulated_action": "LONG",
        "stop_distance_change": "+50%",
        "risk_change": "Wider stop increases per-trade risk by 0.3%"
      }
    ],
    "regime_changes": 0,
    "regime_details": [],
    "risk_impact": {
      "portfolio_var_change": "+0.15%",
      "max_drawdown_change": "+0.08%"
    }
  },
  "verdict": "SAFE_TO_DEPLOY | REVIEW_REQUIRED | BLOCKED",
  "verdict_reason": "4 signals affected with wider stops but no broken paths. Review recommended.",
  "isolation_verified": true
}
```

#### 2.6.4 Verdict Logic

| Verdict | Condition |
|---------|-----------|
| `SAFE_TO_DEPLOY` | 0 broken paths, 0 regime changes, <5% signal score variance |
| `REVIEW_REQUIRED` | 0 broken paths, but >5% signal score variance or risk impact |
| `BLOCKED` | Any broken wiring path or critical gate failure |

#### 2.6.5 Implementation Plan

| Step | Description | File |
|------|-------------|------|
| 1 | Create simulator core (loads baseline, applies diff, runs isolated engine) | `core/change_simulator.py` |
| 2 | Build config differ (compares YAML keys before/after) | `core/change_simulator.py` |
| 3 | Implement wiring path impact analysis | `core/change_simulator.py` |
| 4 | Implement signal comparison (reuses Replay Engine diffing from LUX-002) | `core/change_simulator.py` |
| 5 | Build verdict evaluator (safe/review/blocked logic) | `core/change_simulator.py` |
| 6 | Add CLI entry point: `python -m core.change_simulator --config new_settings.yaml` | `core/change_simulator.py` |
| 7 | Wire War Room UI for on-demand simulation | Dashboard component |

#### 2.6.6 Usage Example (CLI)

```bash
# Simulate a config change against yesterday's data
python -m core.change_simulator \
  --baseline-date 2026-02-26 \
  --new-config config/settings_proposed.yaml

# Simulate current code against a specific historical day
python -m core.change_simulator \
  --baseline-date 2026-02-20 \
  --code-branch feature/new-scoring
```

---

## 3. Priority and Sequencing

```
Phase         Gate                   Features
-----------   ---------------------  -----------------------------------------
W13           Implement with W13     LUX-001  Evidence Pack Export
                                     LUX-003  Incident Library
                                     LUX-004  Manager One-Pager

PAPER STABLE  After paper trading    LUX-005  SLA Dashboard
              passes stability       LUX-006  Change Impact Simulator
              criteria (7d clean)

PAPER READY   After paper trading    LUX-002  Deterministic Replay Mode
              meets go-live gate
              (all readiness items
              resolved)
```

### 3.1 Dependency Graph

```
LUX-001 (Evidence Pack)  -----> standalone, no dependencies
LUX-003 (Incident Library) ---> standalone, no dependencies
LUX-004 (Manager One-Pager) --> standalone, no dependencies

LUX-005 (SLA Dashboard) ------> depends on LUX-003 (breach -> incident)
LUX-006 (Change Simulator) ---> depends on LUX-002 (reuses replay diffing)

LUX-002 (Replay Engine) ------> standalone engine, but benefits from
                                 LUX-001 (evidence packs as replay inputs)
```

### 3.2 Rationale

- **P1 features** (Evidence Pack, Incident Library, Manager One-Pager) provide immediate operational visibility during paper trading. They cost little to build and dramatically reduce "what happened?" investigation time.
- **P2 features** (SLA Dashboard, Change Impact Simulator) require a stable system to measure against. SLAs are meaningless if the system is still being actively debugged. The simulator needs the Incident Library (P1) for breach escalation.
- **P3 feature** (Deterministic Replay) is the most complex feature and requires a mature artifact history to replay against. It delivers maximum value when the system has weeks of paper trading data to replay.

---

## 4. Acceptance Tests

### T-LUX-001: Evidence Pack Export

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-001-A | Click "Export Today's Evidence Pack" in War Room | ZIP file downloads within 10s |
| T-LUX-001-B | Unzip and verify contents | All expected directories present; file count matches META.json |
| T-LUX-001-C | Verify integrity hash | SHA-256 of ZIP contents matches `integrity_hash` in META.json |
| T-LUX-001-D | Export for a date with no artifacts | Returns 404 with clear error message |
| T-LUX-001-E | Auto-prune after 90 days | Packs older than 90 days are deleted on next generation |

### T-LUX-002: Deterministic Replay Mode

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-002-A | Replay yesterday with identical code | All signals match original (0 divergences) |
| T-LUX-002-B | Replay yesterday with modified config | Divergences correctly identified in diff report |
| T-LUX-002-C | Verify isolation: no Telegram messages sent | Telegram dispatch log shows zero entries during replay |
| T-LUX-002-D | Verify isolation: no live state mutated | Portfolio positions, P&L unchanged after replay |
| T-LUX-002-E | Replay a date with missing artifacts | Returns clear error: "Incomplete artifacts for {date}" |
| T-LUX-002-F | Replay cannot trigger another replay | Recursive replay attempt raises `ReplayIsolationError` |

### T-LUX-003: Incident Library

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-003-A | Trigger integrity alert | Incident auto-created in `data/incidents.jsonl` |
| T-LUX-003-B | Resolve incident via API | `resolution`, `resolved_by`, `resolved_at`, `time_to_resolve_minutes` populated |
| T-LUX-003-C | War Room incident log displays correctly | All incidents visible, sortable by severity and date |
| T-LUX-003-D | MTTR calculation | Mean time to resolve matches manual calculation |
| T-LUX-003-E | Related incident linking | Two incidents linked; both show reference in `related_incidents` |

### T-LUX-004: Manager One-Pager

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-004-A | 07:00 UK generation | PDF generated and present in `data/reports/` within 60s of 07:00 |
| T-LUX-004-B | 22:00 UK generation | PDF generated and present in `data/reports/` within 60s of 22:00 |
| T-LUX-004-C | Telegram delivery | `[DAILY BRIEF]` message received with PDF attachment |
| T-LUX-004-D | PDF content accuracy | All 6 sections present; data matches source artifacts |
| T-LUX-004-E | PDF file size | Under 500 KB |
| T-LUX-004-F | Single page constraint | PDF is exactly 1 page (A4 landscape) |

### T-LUX-005: SLA Dashboard

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-005-A | All SLAs green | Dashboard shows green for all metrics when targets met |
| T-LUX-005-B | SLA yellow threshold | Metric at 86% (for 95% target) shows yellow |
| T-LUX-005-C | SLA red breach | Metric below threshold shows red + incident auto-created |
| T-LUX-005-D | Telegram breach alert | `[SLA BREACH]` message sent on red transition |
| T-LUX-005-E | History logging | `data/sla_history.jsonl` contains entry for each measurement cycle |
| T-LUX-005-F | Per-provider uptime | Each data provider tracked independently |

### T-LUX-006: Change Impact Simulator

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-006-A | Config change: no broken paths | Verdict = `SAFE_TO_DEPLOY` when only scores shift |
| T-LUX-006-B | Config change: broken wiring path | Verdict = `BLOCKED` with broken path details |
| T-LUX-006-C | Config change: >5% score variance | Verdict = `REVIEW_REQUIRED` with signal details |
| T-LUX-006-D | Isolation guarantee | No Telegram, no live state mutation during simulation |
| T-LUX-006-E | CLI execution | `python -m core.change_simulator` runs end-to-end |
| T-LUX-006-F | Missing baseline artifacts | Clear error message when reference date lacks artifacts |

### T-LUX-007: Cross-Feature Integration

| ID | Test | Pass Criteria |
|----|------|---------------|
| T-LUX-007-A | SLA breach creates incident | RED SLA auto-creates `sla_breach` incident in Incident Library |
| T-LUX-007-B | Evidence pack includes incidents | Today's incidents appear in evidence pack |
| T-LUX-007-C | One-pager reflects incident count | Manager One-Pager shows open incident count |
| T-LUX-007-D | Replay uses evidence pack artifacts | Replay engine can load from evidence pack ZIP as input |
| T-LUX-007-E | Simulator references incident on block | BLOCKED verdict auto-creates incident in library |

---

## 5. File Map

| Feature | New Files | Modified Files |
|---------|-----------|----------------|
| LUX-001 Evidence Pack | `core/evidence_pack.py`, `api/routes/evidence.py` | Dashboard (new panel) |
| LUX-002 Replay Engine | `core/replay_engine.py` | None (fully isolated) |
| LUX-003 Incident Library | `core/incident_library.py`, `api/routes/incidents.py` | `core/integrity.py`, `core/wiring.py`, `main.py`, `core/readiness_gate.py`, Dashboard (new panel) |
| LUX-004 Manager One-Pager | `delivery/manager_one_pager.py` | `main.py` (scheduler), `core/manifest.py` |
| LUX-005 SLA Dashboard | `core/sla_monitor.py` | `core/incident_library.py` (breach hook), Dashboard (new panel) |
| LUX-006 Change Simulator | `core/change_simulator.py` | None (reuses replay engine internally) |

---

## 6. Estimated Effort

| Feature | Complexity | Estimated Days | Dependencies |
|---------|------------|----------------|--------------|
| LUX-001 Evidence Pack | Low | 1-2 | None |
| LUX-002 Replay Engine | High | 5-7 | Mature artifact history |
| LUX-003 Incident Library | Medium | 2-3 | None |
| LUX-004 Manager One-Pager | Medium | 2-3 | None |
| LUX-005 SLA Dashboard | Medium | 3-4 | LUX-003 |
| LUX-006 Change Simulator | High | 4-5 | LUX-002 |
| **Total** | | **17-24 days** | |

---

*End of document. NZT48-ANNEX-LUX-001 v1.0*
