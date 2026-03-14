# NZT-48 Observability & Monitoring Specification

**Document ID:** NZT48-ANNEX-OMS-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** BINDING -- All observability, metrics export, alerting, and oncall procedures MUST conform to this specification
**Owner:** NZT-48 Trading Engine
**Classification:** Internal / Operational

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Metrics Catalogue](#2-metrics-catalogue)
3. [Logging Standard](#3-logging-standard)
4. [Alerting Rules](#4-alerting-rules)
5. [SLO / SLA Definitions](#5-slo--sla-definitions)
6. [Dashboard Requirements](#6-dashboard-requirements)
7. [Oncall Procedures](#7-oncall-procedures)
8. [Incident Detection](#8-incident-detection)
9. [Acceptance Tests](#9-acceptance-tests)
10. [Cross-References](#10-cross-references)
11. [Revision History](#11-revision-history)

---

## 1. Purpose

### 1.1 Objective

Define the complete observability stack for the NZT-48 trading system: metrics collection, structured logging, distributed tracing, alerting rules, dashboard requirements, Service Level Objectives, Service Level Agreements, and oncall procedures.

### 1.2 Problem Statement

The current monitoring posture is ad-hoc and operationally fragile:

| Gap | Current State | Target State |
|-----|---------------|--------------|
| Metrics export | None. Engine state visible only through War Room API snapshots. | Structured metrics catalogue with defined types, labels, and export endpoint. |
| Logging | Unstructured `print()` and `logging.info()` to stdout. No guaranteed schema. | JSON structured logging with required fields, rotation, and retention policy. |
| Alerting | Telegram notifications exist but lack priority tiers, escalation paths, and deduplication against monitoring alerts. | Four-tier alerting with defined response times, escalation, and backup channels. |
| SLOs | None defined. No quantitative measure of "system is healthy." | Explicit SLOs for scan latency, signal generation, delivery, and uptime. |
| Oncall | Operator checks when they remember. No checklist, no scheduled cadence. | Structured three-check daily cadence with checklists and escalation policy. |
| Dashboards | War Room exists (30+ panels) but lacks metrics-over-time visualisation and health scoring. | Metrics dashboard with historical trends, SLO burn-rate, and composite health score. |

### 1.3 Infrastructure Context

```
+---------------------------------------------------------+
|  EC2 Instance: 54.242.32.11                             |
|                                                         |
|  +------------------+    +-------------------------+    |
|  | nzt48 container  |    | nzt48-dashboard         |    |
|  | Trading Engine   |    | Next.js :3001           |    |
|  | FastAPI :8000    |    | (War Room)              |    |
|  | APScheduler 27+  |    +-------------------------+    |
|  | 60s scan loop    |                                   |
|  +------------------+                                   |
|           |                                             |
|     stdout/stderr --> journald (host)                   |
|     Telegram Bot --> Telegram API                       |
|     /api/* endpoints --> War Room frontend              |
+---------------------------------------------------------+
```

### 1.4 Design Principles

1. **Minimal infrastructure**. The monitoring stack must not introduce new containers, databases, or external services unless the existing stack cannot meet the requirement. A `/api/metrics` endpoint on the existing FastAPI is preferred over a separate Prometheus/Grafana deployment.
2. **Zero interference with trading**. Metrics collection, log writing, and alert dispatch must NEVER block or delay the scan loop, signal processing, or position management.
3. **Fail-open for observability**. If metrics export fails, the engine continues trading. Observability failures are logged and alerted but never halt the engine.
4. **Immutable audit trail**. Metrics snapshots and alert history are append-only. No automated process may delete or modify historical metrics data.

---

## 2. Metrics Catalogue

All metrics use the `nzt48_` prefix. Metric names follow Prometheus naming conventions (lowercase, underscores, unit suffix). Even if Prometheus is not deployed initially, the naming convention ensures future compatibility.

### 2.1 Engine Metrics

| Metric Name | Type | Labels | Description | Source |
|---|---|---|---|---|
| `nzt48_scan_duration_seconds` | Histogram | -- | Wall-clock time for a single scan cycle, from start of ticker iteration to completion of signal evaluation. Buckets: 1, 5, 10, 15, 20, 25, 30, 45, 60, 90, 120. | `main.py` scan loop |
| `nzt48_scan_tickers_scanned` | Gauge | -- | Number of tickers processed in the most recent scan cycle. Expected range: 0-18. | `main.py` scan loop |
| `nzt48_scan_plays_generated` | Gauge | -- | Number of play candidates generated in the most recent scan cycle (pre-veto). | `main.py` scan loop |
| `nzt48_scan_signals_strict` | Gauge | -- | Number of signals that passed all gates and qualified as strict in the most recent scan cycle. | `main.py` scan loop |
| `nzt48_scan_signals_vetoed` | Gauge | -- | Number of signals rejected by firewall, circuit breaker, or other veto mechanisms in the most recent scan cycle. | `main.py` scan loop |
| `nzt48_data_freshness_pct` | Gauge | -- | Percentage of tickers in the active universe whose most recent price data is within the configured TTL. 100% = all data fresh. | Data provider layer |
| `nzt48_data_provider_errors` | Counter | `provider` | Cumulative count of errors from external data providers. Label `provider` values: `yfinance`, `lse_scraper`, `alpha_vantage`, etc. | Data provider layer |
| `nzt48_regime_current` | Gauge | `layer` | Encoded current regime state. Label `layer` values: `market`, `volatility`, `momentum`. Encoding: 0=UNKNOWN, 1=BULL, 2=BEAR, 3=NEUTRAL, 4=EXPANSION, 5=CONTRACTION. | Regime engine |
| `nzt48_drought_state` | Gauge | -- | Current drought state. 0=NORMAL, 1=WATCH, 2=DROUGHT, 3=CRITICAL. | Drought monitor |
| `nzt48_positions_open` | Gauge | -- | Number of currently open virtual positions. | Position manager |
| `nzt48_equity_gbp` | Gauge | -- | Current total equity in GBP (cash + unrealised P&L). | Portfolio tracker |
| `nzt48_daily_pnl_pct` | Gauge | -- | Today's realised + unrealised P&L as a percentage of start-of-day equity. | Portfolio tracker |
| `nzt48_drawdown_pct` | Gauge | -- | Current drawdown from equity peak, as a positive percentage. 0.0 = at peak. | Drawdown monitor |
| `nzt48_circuit_breaker_level` | Gauge | -- | Current circuit breaker level. 0=OK, 1=L1 (1.5% drawdown), 2=L2 (2.5% drawdown), 3=L3 (4.0% drawdown). Per RISK_CONSTITUTION thresholds. | Circuit breaker |
| `nzt48_kill_switch_active` | Gauge | -- | Kill switch state. 0=inactive (trading permitted), 1=active (all trading halted). | Kill switch controller |

### 2.2 Telegram Metrics

| Metric Name | Type | Labels | Description | Source |
|---|---|---|---|---|
| `nzt48_telegram_messages_sent` | Counter | `type` | Cumulative count of successfully sent Telegram messages. Label `type` values correspond to the message type registry in TELEGRAM_TAPE_SPEC (NZT48-ANNEX-002): `SIGNAL`, `PREMARKET_BRIEF`, `FIREWALL_BLOCK`, `DROUGHT_ALERT`, `REGIME_CHANGE`, `NIGHTLY_DIGEST`, `SYSTEM_STATUS`, `KILL_SWITCH`, `ERROR`, `TRADE_CLOSED`, `CONTRADICTION`. | `telegram_bot.py` |
| `nzt48_telegram_messages_failed` | Counter | `type` | Cumulative count of Telegram send failures. Same label values as above. | `telegram_bot.py` |
| `nzt48_telegram_delivery_latency_ms` | Histogram | -- | Time in milliseconds from message dispatch call to Telegram API acknowledgment. Buckets: 50, 100, 200, 500, 1000, 2000, 5000, 10000. | `telegram_bot.py` |

### 2.3 PDF Metrics

| Metric Name | Type | Labels | Description | Source |
|---|---|---|---|---|
| `nzt48_pdf_generated` | Counter | `type` | Cumulative count of PDF reports generated. Label `type` values: `momentum` (PDF1), `risk` (PDF2), `desk_notes`. | PDF delivery modules |
| `nzt48_pdf_qa_pass` | Counter | `type` | Cumulative count of PDFs that passed automated QA checks. Same label values. | PDF QA gate |
| `nzt48_pdf_qa_fail` | Counter | `type` | Cumulative count of PDFs that failed automated QA checks. Same label values. | PDF QA gate |

### 2.4 Infrastructure Metrics

| Metric Name | Type | Labels | Description | Source |
|---|---|---|---|---|
| `nzt48_container_uptime_seconds` | Gauge | -- | Seconds since the nzt48 container last started. Monotonically increasing until restart. | Engine startup timestamp |
| `nzt48_container_restarts` | Counter | -- | Cumulative count of container restarts since initial deployment. Persisted to disk. | Startup detection logic |
| `nzt48_api_request_duration_ms` | Histogram | `endpoint` | FastAPI request duration in milliseconds. Label `endpoint` = route path (e.g., `/api/signals`, `/api/regime`). Buckets: 5, 10, 25, 50, 100, 200, 500, 1000, 2000. | FastAPI middleware |
| `nzt48_api_errors` | Counter | `endpoint`, `status` | Cumulative count of API errors. Label `endpoint` = route path, `status` = HTTP status code (4xx, 5xx). | FastAPI middleware |
| `nzt48_scheduler_jobs_active` | Gauge | -- | Number of APScheduler jobs in active (non-paused) state. Expected: 27+. | APScheduler introspection |
| `nzt48_scheduler_job_failures` | Counter | `job` | Cumulative count of APScheduler job execution failures. Label `job` = job ID. | APScheduler error handler |
| `nzt48_disk_usage_bytes` | Gauge | -- | Total size of the `artifacts/` directory in bytes. Sampled every 300 seconds. | Periodic disk check |
| `nzt48_memory_usage_bytes` | Gauge | -- | RSS memory usage of the nzt48 process in bytes. Sampled every 60 seconds. | `psutil` or `/proc/self/status` |

### 2.5 Metrics Export

**Endpoint**: `GET /api/metrics`

**Format**: JSON object containing all metrics with their current values, types, labels, and a `collected_at` ISO 8601 timestamp.

```json
{
  "collected_at": "2026-02-27T14:30:00Z",
  "metrics": {
    "nzt48_scan_duration_seconds": {
      "type": "histogram",
      "buckets": { "1": 0, "5": 12, "10": 45, "15": 88, "30": 100 },
      "sum": 1234.56,
      "count": 100
    },
    "nzt48_scan_tickers_scanned": {
      "type": "gauge",
      "value": 12
    },
    "nzt48_data_provider_errors": {
      "type": "counter",
      "values": {
        "yfinance": 3,
        "lse_scraper": 0
      }
    }
  }
}
```

**Response contract**:

| Field | Type | Required | Description |
|---|---|---|---|
| `collected_at` | string (ISO 8601) | YES | Timestamp of metric snapshot |
| `metrics` | object | YES | Map of metric name to metric payload |
| `metrics.<name>.type` | string | YES | One of: `gauge`, `counter`, `histogram` |
| `metrics.<name>.value` | number | For gauge/counter without labels | Current value |
| `metrics.<name>.values` | object | For counter/gauge with labels | Map of label value to current value |
| `metrics.<name>.buckets` | object | For histogram | Map of bucket boundary to cumulative count |
| `metrics.<name>.sum` | number | For histogram | Sum of all observed values |
| `metrics.<name>.count` | number | For histogram | Total number of observations |

**Performance constraint**: The `/api/metrics` endpoint MUST return within 100ms. All metrics are maintained in-memory and the endpoint performs no I/O.

**Optional Prometheus exposition**: If Prometheus is deployed in future, add `GET /api/metrics/prometheus` returning Prometheus exposition format (`text/plain; version=0.0.4`). This endpoint is NOT required for initial implementation.

### 2.6 Metrics Persistence

Metrics are ephemeral in-memory by default. To support historical queries and SLO tracking:

1. Every 60 seconds, the engine writes a metrics snapshot to `artifacts/metrics/metrics_<YYYYMMDD>.jsonl` (one JSON line per snapshot).
2. Retention: 30 days of JSONL files. Files older than 30 days are deleted by the daily cleanup job.
3. File size estimate: ~2KB per snapshot x 1440 snapshots/day = ~2.8MB/day, ~84MB/month.
4. The JSONL file is the authoritative source for SLO compliance calculation.

---

## 3. Logging Standard

### 3.1 Format

ALL log output from the nzt48 engine MUST be JSON structured. No unstructured `print()` statements in production code. Existing `print()` calls must be migrated to the structured logger.

**Schema**:

```json
{
  "ts": "2026-02-27T14:30:00.123Z",
  "level": "INFO",
  "component": "scan_loop",
  "msg": "Scan cycle completed",
  "run_id": "scan_20260227_143000",
  "data": {
    "tickers_scanned": 12,
    "plays_generated": 3,
    "signals_strict": 1,
    "duration_s": 8.42
  }
}
```

### 3.2 Required Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `ts` | string (ISO 8601, ms precision) | YES | UTC timestamp of the log event |
| `level` | string | YES | One of: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `component` | string | YES | Subsystem emitting the log. Values: `scan_loop`, `regime_engine`, `drought_monitor`, `position_manager`, `signal_evaluator`, `circuit_breaker`, `kill_switch`, `telegram`, `pdf_delivery`, `api`, `scheduler`, `learning`, `self_healing`, `data_provider`, `startup`, `shutdown` |
| `msg` | string | YES | Human-readable message. Concise. No variable interpolation in the message string itself -- variables go in `data`. |
| `run_id` | string | When applicable | Unique identifier linking related log entries across a single operation (e.g., one scan cycle, one signal evaluation chain). Format: `<operation>_<YYYYMMDD>_<HHMMSS>`. |
| `data` | object | When applicable | Structured key-value pairs with operation-specific context. |

### 3.3 Log Levels

| Level | Usage | Production Default | Examples |
|---|---|---|---|
| `DEBUG` | Verbose diagnostic detail. Per-ticker calculations, intermediate scores, raw API responses. | OFF (enable via config flag `logging.debug_enabled: true`) | "Ticker QQQ3.L momentum score raw components", "yfinance response payload" |
| `INFO` | Standard operational events. Scan completions, signal generations, position changes, regime transitions. | ON | "Scan cycle completed", "Signal generated: BUY QQQ3.L", "Position opened" |
| `WARNING` | Degraded but functional. Data staleness, provider timeout with fallback success, approaching resource limits. | ON | "Data freshness below 80%", "yfinance timeout, using cached data", "Disk usage 75%" |
| `ERROR` | Requires attention. Unhandled exceptions, provider failures without fallback, alert delivery failures. | ON | "yfinance provider failed, no fallback available", "Telegram send failed after 3 retries" |
| `CRITICAL` | System halt or near-halt. Kill switch activation, circuit breaker L3, unrecoverable state corruption. | ON | "Kill switch activated", "Circuit breaker L3 triggered", "State file corrupted" |

### 3.4 Log Rotation

| Parameter | Value |
|---|---|
| Max file size | 100 MB |
| Max retained files | 10 |
| Compression | gzip after 24 hours |
| Total max disk usage | ~1 GB (10 x 100MB uncompressed worst case; ~300MB typical with compression) |
| Rotation mechanism | Python `logging.handlers.RotatingFileHandler` with post-rotation compression hook |
| Output targets | stdout (for `docker logs`) AND file (`/app/logs/nzt48.log`) |

### 3.5 Sensitive Data Policy

The following data MUST NEVER appear in log output at any level:

| Data Type | Handling |
|---|---|
| API keys (Telegram, data providers) | Mask entirely. Log `"api_key": "***MASKED***"` |
| Full position details in plain text | Log ticker and direction only. No exact quantities or prices in DEBUG/INFO. ERROR/CRITICAL may include quantities for diagnosis. |
| Account credentials | Never logged under any circumstance |
| EC2 instance metadata tokens | Never logged under any circumstance |

### 3.6 Log Aggregation Path

```
nzt48 container (Python logger)
    |
    +--> stdout (captured by Docker)
    |       |
    |       +--> docker logs nzt48 (operator access)
    |       |
    |       +--> journald on EC2 host (automatic via Docker logging driver)
    |
    +--> /app/logs/nzt48.log (within container, rotated)
```

Future enhancement: ship journald entries to CloudWatch Logs via the CloudWatch agent. Not required for initial implementation.

---

## 4. Alerting Rules

### 4.1 Alert Architecture

Alerts are evaluated within the nzt48 engine process. Each alerting rule runs as a check within the existing APScheduler framework, evaluated on the intervals specified below. Alerts dispatch via the Telegram delivery layer defined in NZT48-ANNEX-002 (Telegram Tape Spec).

**Alert deduplication**: Alerting rules maintain a cooldown window per alert ID. Within the cooldown, duplicate conditions do not trigger additional notifications. Cooldown values are specified per rule.

**Backup channel**: If Telegram delivery itself is failing (meta-failure), P1 alerts fall back to writing to `artifacts/alerts/pending_alerts.jsonl` for operator retrieval via the War Room API.

### 4.2 Priority 1 -- CRITICAL (Immediate Response Required)

Response time: **Immediate** (operator must acknowledge within 5 minutes during LSE hours).

| Alert ID | Condition | Metric/Source | Cooldown | Notification Channel | Message Template |
|---|---|---|---|---|---|
| A1 | Kill switch activated | `nzt48_kill_switch_active` transitions 0 -> 1 | NO COOLDOWN | Telegram `[KILL]` + email | `[P1-A1] KILL SWITCH ACTIVATED at {ts}. Trigger: {reason}. ALL TRADING HALTED. Manual intervention required.` |
| A2 | Circuit breaker L3 triggered | `nzt48_circuit_breaker_level` transitions to 3 | 30 min | Telegram `[SYSTEM]` + email | `[P1-A2] CIRCUIT BREAKER L3 (4.0% drawdown). Daily P&L: {pnl}%. Equity: {equity}. Trading suspended per Risk Constitution.` |
| A3 | Container crash or restart | `nzt48_container_restarts` incremented OR container uptime reset to 0 | 5 min | Telegram `[SYSTEM]` | `[P1-A3] CONTAINER RESTART detected at {ts}. Uptime was: {prev_uptime}. Restart count: {count}. Verifying state integrity...` |
| A4 | Zero scans for 5 minutes during market hours | `nzt48_scan_tickers_scanned` = 0 for 5 consecutive minutes AND LSE session active (08:00-16:30 UK) | 10 min | Telegram `[SYSTEM]` | `[P1-A4] SCAN LOOP STALLED. Zero tickers scanned for {duration} during LSE hours. Last successful scan: {last_scan_ts}.` |
| A5 | Data coverage below 50% | `nzt48_data_freshness_pct` < 50 for 2 consecutive checks | 15 min | Telegram `[SYSTEM]` | `[P1-A5] DATA COVERAGE CRITICAL: {pct}% of tickers have fresh data. Threshold: 50%. Providers affected: {providers}.` |

### 4.3 Priority 2 -- HIGH (Respond Within 30 Minutes)

Response time: **30 minutes** during LSE hours.

| Alert ID | Condition | Metric/Source | Cooldown | Notification Channel | Message Template |
|---|---|---|---|---|---|
| A6 | Circuit breaker L1 or L2 triggered | `nzt48_circuit_breaker_level` transitions to 1 or 2 | 30 min | Telegram `[SYSTEM]` | `[P2-A6] Circuit breaker L{level} triggered. Daily P&L: {pnl}%. Drawdown: {dd}%. Restrictions active.` |
| A7 | Telegram delivery failure rate > 30% | Rolling 30-min failure rate of `nzt48_telegram_messages_failed` / total > 0.30 | 60 min | Telegram backup channel (see 4.1) | `[P2-A7] Telegram delivery degraded: {fail_rate}% failure rate over last 30 min. {failed_count}/{total_count} messages failed.` |
| A8 | API endpoint returning 5xx | `nzt48_api_errors` with `status` >= 500, 3+ errors in 5 minutes for same endpoint | 15 min | Telegram `[ERROR]` | `[P2-A8] API endpoint {endpoint} returning 5xx errors. Count: {count} in last 5 min. Latest status: {status}.` |
| A9 | Data provider down for > 10 minutes | `nzt48_data_provider_errors` increasing AND zero successful fetches from provider for 10 min | 30 min | Telegram `[ERROR]` | `[P2-A9] Data provider {provider} DOWN for {duration}. Errors: {count}. Fallback status: {fallback}.` |
| A10 | Scheduler job missed | APScheduler reports job execution missed (job not invoked within 2x its configured interval) | 30 min | Telegram `[SYSTEM]` | `[P2-A10] Scheduler job missed: {job_id}. Expected interval: {interval}. Last execution: {last_run}.` |

### 4.4 Priority 3 -- MEDIUM (Respond Within 2 Hours)

Response time: **2 hours** during LSE hours.

| Alert ID | Condition | Metric/Source | Cooldown | Notification Channel | Message Template |
|---|---|---|---|---|---|
| A11 | Regime contradiction detected | Regime layers report contradictory states (e.g., market=BULL + momentum=BEAR) for > 15 minutes | 60 min | Telegram `[SYSTEM]` | `[P3-A11] Regime contradiction: {layer1}={state1}, {layer2}={state2}. Duration: {duration}. Review regime engine.` |
| A12 | PDF QA failure | `nzt48_pdf_qa_fail` incremented | 60 min | Telegram `[ERROR]` | `[P3-A12] PDF QA failed for {type} report. Reason: {reason}. PDF not delivered.` |
| A13 | Learning engine drift alert | Learning engine detects parameter drift beyond configured threshold | 120 min | Telegram `[SYSTEM]` | `[P3-A13] Learning engine drift: {parameter} shifted {delta} from baseline. Current: {current}, baseline: {baseline}.` |
| A14 | Disk usage > 80% | `nzt48_disk_usage_bytes` exceeds 80% of allocated volume | 120 min | Telegram `[SYSTEM]` | `[P3-A14] Disk usage at {pct}%. Artifacts directory: {size_mb}MB. Consider cleanup.` |

### 4.5 Priority 4 -- LOW (Daily Review)

Response time: **Next business day** (included in daily digest).

| Alert ID | Condition | Metric/Source | Aggregation | Notification Channel |
|---|---|---|---|---|
| A15 | Data staleness warnings (not rejections) | `nzt48_data_freshness_pct` between 50-80% at any point during session | Count of occurrences, min freshness observed | Daily digest Telegram `[DIGEST]` |
| A16 | Low signal quality trend | Strict signals declining over 5-day rolling window | 5-day trend summary | Daily digest Telegram `[DIGEST]` |
| A17 | Provider rate limiting | `nzt48_data_provider_errors` with rate-limit-specific error codes | Provider, count, windows affected | Daily digest Telegram `[DIGEST]` |

### 4.6 Alert State Machine

Each alert instance follows this lifecycle:

```
  CLEAR ──(condition met)──> FIRING ──(notification sent)──> NOTIFIED
    ^                                                           |
    |                                                           |
    +───────────(condition clears for 2x cooldown)──────────────+
```

- **CLEAR**: Condition not met. No action.
- **FIRING**: Condition met. Notification queued.
- **NOTIFIED**: Notification sent. Cooldown active. Repeat firings within cooldown are suppressed.
- Transition back to CLEAR requires the condition to be absent for at least 2x the cooldown duration (hysteresis to prevent flapping).

---

## 5. SLO / SLA Definitions

### 5.1 Definitions

- **SLO (Service Level Objective)**: Internal target that the system aims to meet. Breaches are tracked and reviewed but do not trigger immediate action beyond alerting.
- **SLA (Service Level Agreement)**: Binding commitment. Persistent SLA breach indicates a system defect that must be investigated and resolved.
- **Measurement window**: All SLOs/SLAs are measured over rolling 7-day windows unless otherwise stated. LSE session hours: 08:00-16:30 UK time, Mon-Fri, excluding UK bank holidays.

### 5.2 Scan SLA

| Parameter | Value |
|---|---|
| Definition | Percentage of scan cycles that complete within 30 seconds |
| Target | 95% of scan cycles within 30 seconds |
| Measurement | `nzt48_scan_duration_seconds` histogram, p95 <= 30s |
| Window | Rolling 7-day, LSE hours only |
| Breach action | P3 alert if weekly compliance drops below 95%. P2 alert if below 85%. |
| Data source | Metrics JSONL persistence file |

### 5.3 Signal SLA

| Parameter | Value |
|---|---|
| Definition | The system generates at least 1 strict signal per LSE session on a defined percentage of trading days |
| Target | >= 1 strict signal on 80% of trading days |
| Measurement | Daily count of `nzt48_scan_signals_strict` > 0 |
| Window | Rolling 20 trading days |
| Breach action | P4 daily digest note if below 80% over 20-day window. Drought system (NZT48-ANNEX-REGIME-DROUGHT-001) handles operational response. |
| Exclusions | Days where kill switch was active for > 50% of LSE hours are excluded from the denominator |

### 5.4 Telegram SLA

| Parameter | Value |
|---|---|
| Delivery success rate | 95% of messages successfully delivered (ACK from Telegram API) |
| Delivery latency | P1 alerts delivered within 5 seconds of dispatch. All messages delivered within 10 seconds p95. |
| Measurement | `nzt48_telegram_messages_sent` / (`nzt48_telegram_messages_sent` + `nzt48_telegram_messages_failed`) |
| Window | Rolling 7-day |
| Breach action | Success rate < 95% triggers A7 (P2). Latency p95 > 10s triggers P3 alert. |

### 5.5 PDF SLA

| Parameter | Value |
|---|---|
| Definition | Percentage of scheduled PDF reports that are generated AND pass QA |
| Target | 90% of scheduled PDFs generated and QA-passed |
| Measurement | `nzt48_pdf_qa_pass` / (`nzt48_pdf_qa_pass` + `nzt48_pdf_qa_fail` + scheduled_but_not_generated) |
| Window | Rolling 7-day |
| Breach action | P4 daily digest note. Persistent breach (< 90% for 3 consecutive weeks) escalates to P3. |

### 5.6 API SLA

| Parameter | Value |
|---|---|
| Uptime | 99% during LSE hours (measured as: minutes with >= 1 successful health check / total LSE minutes) |
| Latency | p95 < 200ms for all read endpoints |
| Measurement | `nzt48_api_request_duration_ms` histogram p95, health check success rate |
| Window | Rolling 7-day, LSE hours only |
| Breach action | Uptime < 99% triggers P3 alert. Latency p95 > 200ms for > 30 minutes triggers P3 alert. |

### 5.7 Uptime SLA

| Parameter | Value |
|---|---|
| Definition | nzt48 container uptime as percentage of LSE session hours |
| Target | 95% uptime during LSE hours (measured weekly) |
| Measurement | `nzt48_container_uptime_seconds` tracked against total LSE minutes in the week |
| Window | Rolling 7-day (Mon 08:00 to Fri 16:30 UK) |
| Breach action | < 95% triggers P2 alert with root cause investigation required |

### 5.8 Outcome SLA

| Parameter | Value |
|---|---|
| Scalp resolution | Scalp trades (S15 daily target) resolved within 2 hours of entry |
| Swing resolution | Swing trades resolved within 1 LSE session (same-day close) |
| Measurement | Trade duration from entry to exit, categorised by strategy type |
| Window | Rolling 20 trading days |
| Breach action | > 10% of trades exceeding resolution target triggers P4 review note |

### 5.9 SLO Burn-Rate Dashboard

The `/api/slo-status` endpoint returns current compliance for all SLOs:

```json
{
  "as_of": "2026-02-27T16:30:00Z",
  "window_days": 7,
  "slos": {
    "scan_latency": { "target_pct": 95.0, "actual_pct": 97.2, "status": "MET" },
    "signal_generation": { "target_pct": 80.0, "actual_pct": 85.0, "status": "MET" },
    "telegram_delivery": { "target_pct": 95.0, "actual_pct": 98.1, "status": "MET" },
    "pdf_qa": { "target_pct": 90.0, "actual_pct": 88.5, "status": "BREACH" },
    "api_uptime": { "target_pct": 99.0, "actual_pct": 99.5, "status": "MET" },
    "container_uptime": { "target_pct": 95.0, "actual_pct": 99.8, "status": "MET" }
  }
}
```

---

## 6. Dashboard Requirements

### 6.1 Existing War Room Enhancements

The War Room dashboard (NZT48-ANNEX-003) currently has 30+ implemented panels. The following panels are MISSING and MUST be added to achieve full observability coverage:

#### 6.1.1 Scan Health Panel

| Attribute | Value |
|---|---|
| API Endpoint | `GET /api/scan-health` |
| Poll Interval | 15 seconds |
| Content | Last 60 scan cycles: duration sparkline, tickers scanned, plays generated, signals strict, signals vetoed. Highlight scans exceeding 30s SLA in red. Show SLA compliance percentage. |
| Layout | Sparkline chart (60 points) + summary stats row |

#### 6.1.2 Telegram Tape Panel

| Attribute | Value |
|---|---|
| API Endpoint | `GET /api/telegram-tape?hours=24&limit=50` |
| Poll Interval | 30 seconds |
| Content | Chronological feed of all Telegram messages sent. Each entry shows: timestamp, type label, delivery status (sent/failed), latency. Failed messages highlighted in red. |
| Layout | Scrollable table with type-based colour coding per TELEGRAM_TAPE_SPEC |

#### 6.1.3 Consistency Check Panel

| Attribute | Value |
|---|---|
| API Endpoint | `GET /api/consistency-check` |
| Poll Interval | 60 seconds |
| Content | Cross-validation of system state: regime vs drought state coherence, circuit breaker vs kill switch consistency, position count vs position list length, scheduler job count vs expected count. Each check shows PASS/FAIL. |
| Layout | Checklist with green/red indicators |

#### 6.1.4 Go-Live Gate Panel

| Attribute | Value |
|---|---|
| API Endpoint | `GET /api/go-live-gate` |
| Poll Interval | 300 seconds |
| Content | Readiness assessment for transition from paper to live trading. Checks: 30-day uptime > 95%, SLO compliance all MET, zero L3 circuit breaker events in 14 days, zero unresolved P1 alerts, positive cumulative P&L, learning engine converged. Overall: READY / NOT READY with blocking reasons. |
| Layout | Progress bar + checklist |

### 6.2 Metrics Dashboard (NEW)

**Decision: Option C (Minimal Infrastructure)**

The metrics dashboard is implemented as a new page within the existing War Room Next.js application. No additional containers (Prometheus, Grafana) are required.

| Attribute | Value |
|---|---|
| URL | `http://<host>:3001/metrics` |
| Data source | `GET /api/metrics` (current snapshot) + `GET /api/metrics/history?hours=24` (JSONL-backed) |
| Refresh | 30 seconds |

**Required panels on the metrics page**:

| # | Panel | Visualisation | Data Source |
|---|---|---|---|
| 1 | Scan Duration Over Time | Line chart (24h) | `nzt48_scan_duration_seconds` history |
| 2 | Tickers Scanned & Signals | Stacked bar chart (24h) | `nzt48_scan_tickers_scanned`, `nzt48_scan_signals_strict`, `nzt48_scan_signals_vetoed` history |
| 3 | Data Freshness | Area chart (24h) | `nzt48_data_freshness_pct` history |
| 4 | Equity Curve | Line chart (7d) | `nzt48_equity_gbp` history |
| 5 | Telegram Delivery | Success/failure bar chart (24h) | `nzt48_telegram_messages_sent`, `nzt48_telegram_messages_failed` history |
| 6 | API Latency | Heatmap or percentile lines (24h) | `nzt48_api_request_duration_ms` history |
| 7 | SLO Compliance | Traffic light grid | `/api/slo-status` |
| 8 | Resource Usage | Dual-axis line (memory + disk) (24h) | `nzt48_memory_usage_bytes`, `nzt48_disk_usage_bytes` history |

### 6.3 Future Option: Grafana + Prometheus

If the system scales beyond what the in-app metrics page can handle (e.g., multi-instance deployment, long-term retention, advanced alerting):

1. Deploy Prometheus as a Docker container on the same EC2 instance.
2. Expose `GET /api/metrics/prometheus` in Prometheus exposition format.
3. Deploy Grafana as a Docker container, datasource = Prometheus.
4. Import pre-built dashboards from `config/grafana/` (to be created when needed).
5. This migration is NOT required until live trading or multi-instance deployment.

---

## 7. Oncall Procedures

### 7.1 Operator Model

**Current phase (paper trading)**: Single operator. No formal rotation.

**Future phase (live trading)**: Two-person rotation with 24-hour coverage during LSE hours. Handoff at 12:00 UK.

### 7.2 Daily Check Schedule

| Check | Time (UK) | Duration Target | Description |
|---|---|---|---|
| Morning Pre-Market | 08:00 | < 5 minutes | Verify engine running, scan producing, regime sensible, no overnight alerts |
| Midday | 12:00 | < 3 minutes | Verify signals flowing, positions monitored, no circuit breakers |
| EOD Post-Close | 16:30 | < 5 minutes | Verify all positions closed, daily P&L, review PDF reports |

### 7.3 Morning Pre-Market Checklist (08:00 UK)

Execute in order. STOP and investigate if any check fails.

| # | Check | How | Expected | Fail Action |
|---|---|---|---|---|
| 1 | Engine running | `docker ps` -- nzt48 container status is `Up` | Status: Up, uptime > 12h (since last restart) | Restart container. If restart fails, investigate logs. |
| 2 | Scan producing | War Room > Scan Health panel OR `GET /api/scan-health` | Last scan < 2 minutes ago, tickers_scanned > 0 | Check scheduler, check data providers. |
| 3 | No P1/P2 alerts | Telegram history since last EOD check | Zero unacknowledged P1/P2 alerts | Investigate each alert. |
| 4 | Regime sensible | War Room > Market Regime panel | Regime populated, no UNKNOWN states | Check regime engine logs. |
| 5 | Kill switch inactive | War Room > System Health panel OR `GET /api/system-health` | `kill_switch_active: false` | Determine why kill switch was activated. Only deactivate after confirming root cause resolved. |
| 6 | Circuit breaker clear | War Room > Drawdown Status panel | `circuit_breaker_level: 0` | If CB was triggered overnight (should not happen outside LSE hours), investigate. |
| 7 | Data freshness | War Room > Scan Health or `/api/metrics` | `data_freshness_pct >= 80%` | Check provider connectivity. |
| 8 | Disk space | `df -h` on EC2 host | Usage < 80% | Run cleanup: remove old artifacts, compress logs. |

### 7.4 Midday Checklist (12:00 UK)

| # | Check | How | Expected | Fail Action |
|---|---|---|---|---|
| 1 | Signals generated today | War Room > Signal Feed | >= 1 signal since 08:00 | Acceptable if drought state. Otherwise investigate scan quality. |
| 2 | Positions monitored | War Room > Virtual Positions | If positions open: prices updating, P&L calculating | Check data freshness for held tickers. |
| 3 | No circuit breakers | War Room > Drawdown Status | Level 0 | If L1/L2: review positions, confirm restrictions active. |
| 4 | Telegram flowing | War Room > Telegram Tape (once added) | Messages delivered in last 2 hours | Check Telegram bot connectivity. |

### 7.5 EOD Post-Close Checklist (16:30 UK)

| # | Check | How | Expected | Fail Action |
|---|---|---|---|---|
| 1 | All positions closed | War Room > Virtual Positions | Zero open positions | If positions remain open: investigate. Intraday system should not hold overnight. |
| 2 | Daily P&L recorded | War Room > Performance panel | Today's P&L calculated and stored | Check performance tracker. |
| 3 | PDF reports generated | Check Telegram for PDF delivery OR `ls artifacts/pdf/` | Both PDF1 (momentum) and PDF2 (risk) present for today | Check PDF generation logs. Trigger manual regeneration if needed. |
| 4 | SLO compliance | `GET /api/slo-status` | All SLOs status: MET | Note any BREACH items. Add to next-day investigation queue. |
| 5 | Review nightly digest | Telegram `[DIGEST]` message | Digest received with complete daily summary | Check nightly digest job in scheduler. |

### 7.6 Escalation Policy

| Condition | Action |
|---|---|
| Operator unavailable for > 4 hours during LSE session | Kill switch auto-activates via heartbeat timeout (operator must send heartbeat every 4 hours via Telegram command or War Room button). |
| P1 alert unacknowledged for > 15 minutes | Re-send alert every 5 minutes. After 30 minutes with no acknowledgment, activate kill switch automatically. |
| P2 alert unacknowledged for > 60 minutes | Escalate to P1 priority. Begin P1 escalation chain. |
| System enters L3 circuit breaker + operator unresponsive | Kill switch activates automatically. No further action until operator confirms state review. |

### 7.7 Heartbeat Mechanism

The operator heartbeat prevents the system from operating unattended:

| Parameter | Value |
|---|---|
| Heartbeat sources | Telegram `/heartbeat` command, War Room heartbeat button (`POST /api/operator-heartbeat`), or any operator action that writes to the heartbeat file |
| Heartbeat interval | Every 4 hours during LSE session (08:00, 12:00, 16:00 minimum) |
| Grace period | 30 minutes after missed heartbeat before kill switch activation |
| Auto-kill | If no heartbeat received for 4h30m, system activates kill switch and sends P1 alert to all channels |
| Override | Operator can set `operator_away: true` via config, which extends heartbeat to 8 hours and reduces trading to zero-risk mode (no new positions) |

---

## 8. Incident Detection

### 8.1 Detection Methods

| Method | Trigger | Latency | Coverage |
|---|---|---|---|
| Automated alerting | Metric thresholds crossed (Section 4) | < 60 seconds from condition onset to notification | All defined alert rules (A1-A17) |
| Manual observation | Operator observes anomaly in War Room dashboard or Telegram feed | Dependent on operator check schedule | Anything visible in War Room or Telegram |
| Scheduled health check | Daily automated health check at 07:00 UK (pre-market) | Once daily | Comprehensive system state validation |
| State cross-check | Automated comparison of `system_state.json` vs expected state derived from metrics | Every 60 seconds | State file integrity, metric-state consistency |

### 8.2 Daily Pre-Market Health Check (07:00 UK)

An automated job at 07:00 UK (1 hour before LSE open) performs the following checks and sends a consolidated report via Telegram:

| # | Check | Pass Condition |
|---|---|---|
| 1 | Container uptime | > 1 hour (survived overnight) |
| 2 | Scheduler jobs | All 27+ jobs registered and active |
| 3 | Data provider connectivity | At least 1 provider responds successfully |
| 4 | State file integrity | `system_state.json` parseable, schema-valid, last_updated < 5 min ago |
| 5 | Disk space | < 80% usage |
| 6 | Memory usage | < 80% of container limit |
| 7 | Yesterday's SLO compliance | All SLOs MET (or known-accepted BREACH) |
| 8 | Pending alerts | Zero unresolved P1/P2 alerts |
| 9 | Kill switch | Inactive |
| 10 | Circuit breaker | Level 0 |

**Report format**: Single Telegram message with `[HEALTH]` label. Each check shows PASS/FAIL. Overall status: ALL CLEAR or ATTENTION REQUIRED.

### 8.3 State Cross-Check

Every 60 seconds, the engine compares the in-memory state against `system_state.json` (the persisted state file):

| Field | Check | Failure Action |
|---|---|---|
| `kill_switch` | In-memory matches file | If mismatch: adopt the MORE restrictive state (kill switch ON). Log `CRITICAL`. Trigger A1. |
| `circuit_breaker_level` | In-memory matches file | If mismatch: adopt the HIGHER level. Log `ERROR`. Trigger A2 or A6 as appropriate. |
| `positions` | In-memory position count matches file | If mismatch: log `ERROR`. Do NOT auto-correct. Alert operator for manual reconciliation. |
| `regime` | In-memory regime matches file | If mismatch: log `WARNING`. Regime is re-derived from latest data on next scan. No immediate action. |

### 8.4 Incident Response Template

When an incident is detected, the operator records the following in `artifacts/incidents/incident_<YYYYMMDD>_<HHMMSS>.json`:

```json
{
  "incident_id": "INC-20260227-143000",
  "detected_at": "2026-02-27T14:30:00Z",
  "detection_method": "automated_alert",
  "alert_id": "A4",
  "priority": "P1",
  "description": "Scan loop stalled for 5 minutes during LSE session",
  "root_cause": "yfinance rate limit caused all ticker fetches to timeout",
  "resolution": "Increased yfinance request spacing to 500ms. Scan resumed.",
  "resolved_at": "2026-02-27T14:45:00Z",
  "duration_minutes": 15,
  "impact": "Missed 5 scan cycles. No signals lost (drought state was WATCH).",
  "follow_up": ["Add yfinance rate-limit retry with exponential backoff", "Add provider-specific timeout metric"]
}
```

---

## 9. Acceptance Tests

Each test validates a specific capability defined in this specification. Tests are executed as part of system validation before any major deployment.

### OMS-T01: Metrics Endpoint Exposes All Engine Metrics

| Attribute | Value |
|---|---|
| Test ID | OMS-T01 |
| Requirement | Section 2.5 |
| Procedure | 1. Start engine. 2. Wait for at least 1 scan cycle to complete. 3. `GET /api/metrics`. 4. Verify response contains ALL metrics from Section 2.1-2.4. 5. Verify each metric has correct `type` field. 6. Verify response includes `collected_at` timestamp. |
| Pass criteria | All 28 metrics present in response. All types correct. Response returns within 100ms. |
| Automation | `tests/test_observability.py::test_metrics_endpoint_completeness` |

### OMS-T02: P1 Alert Triggers Telegram Within 60 Seconds

| Attribute | Value |
|---|---|
| Test ID | OMS-T02 |
| Requirement | Section 4.2 |
| Procedure | 1. Simulate kill switch activation (A1). 2. Record timestamp of activation. 3. Monitor Telegram channel. 4. Record timestamp of message receipt. 5. Verify latency < 60 seconds. 6. Verify message matches template. |
| Pass criteria | Telegram notification received within 60 seconds. Message contains `[P1-A1]` prefix and all template fields populated. |
| Automation | `tests/test_observability.py::test_p1_alert_latency` |

### OMS-T03: Container Restart Generates Alert

| Attribute | Value |
|---|---|
| Test ID | OMS-T03 |
| Requirement | Section 4.2 (A3) |
| Procedure | 1. Record current `nzt48_container_restarts` value. 2. Restart nzt48 container (`docker-compose restart nzt48`). 3. Monitor Telegram channel. 4. Verify A3 alert received. 5. Verify `nzt48_container_restarts` incremented by 1. |
| Pass criteria | A3 alert received via Telegram within 120 seconds of restart. Restart counter incremented. |
| Automation | `tests/test_observability.py::test_container_restart_alert` (requires Docker access) |

### OMS-T04: Scan SLA Tracked Over 24-Hour Period

| Attribute | Value |
|---|---|
| Test ID | OMS-T04 |
| Requirement | Section 5.2 |
| Procedure | 1. Let engine run for 24 hours. 2. `GET /api/slo-status`. 3. Verify `scan_latency` SLO present. 4. Verify `actual_pct` calculated from `nzt48_scan_duration_seconds` histogram data. 5. Verify compliance computed correctly (scans <= 30s / total scans). |
| Pass criteria | SLO compliance percentage matches manual calculation from JSONL metrics file within 1% tolerance. |
| Automation | `tests/test_observability.py::test_scan_slo_calculation` |

### OMS-T05: Operator Morning Check Completes in Under 5 Minutes

| Attribute | Value |
|---|---|
| Test ID | OMS-T05 |
| Requirement | Section 7.3 |
| Procedure | 1. Operator executes morning checklist (Section 7.3) using War Room dashboard only (no SSH required for standard checks). 2. Time from first check to last check. |
| Pass criteria | All 8 checks completed within 5 minutes. No check requires SSH or CLI access during normal operation (SSH only needed if a check fails). |
| Automation | Manual test. Timed by operator. |

### OMS-T06: Structured JSON Logs Parseable by jq

| Attribute | Value |
|---|---|
| Test ID | OMS-T06 |
| Requirement | Section 3.1, 3.2 |
| Procedure | 1. Collect 100 lines from `docker logs nzt48`. 2. Pipe through `jq '.'`. 3. Verify zero parse errors. 4. Verify every line contains required fields: `ts`, `level`, `component`, `msg`. 5. Verify `ts` is valid ISO 8601. 6. Verify `level` is one of the defined values. |
| Pass criteria | 100% of log lines are valid JSON. 100% contain all required fields. Zero parse errors from `jq`. |
| Automation | `tests/test_observability.py::test_log_json_schema` |

### OMS-T07: Log Rotation Prevents Disk Exhaustion

| Attribute | Value |
|---|---|
| Test ID | OMS-T07 |
| Requirement | Section 3.4 |
| Procedure | 1. Configure log level to DEBUG to generate high volume. 2. Generate sustained log output for 1 hour. 3. Verify log directory size does not exceed 1.1 GB (10 x 100MB + 10% tolerance). 4. Verify oldest file is rotated and compressed. 5. Verify no more than 10 log files exist. |
| Pass criteria | Total log directory size < 1.1 GB. File count <= 10 (plus compressed archives). Compressed files have `.gz` extension. |
| Automation | `tests/test_observability.py::test_log_rotation` |

---

## 10. Cross-References

| Document | Relevance |
|---|---|
| NZT48-ANNEX-002 (Telegram Tape Spec) | Telegram message types, templates, deduplication, and rate limits. Alert notifications use the Telegram delivery layer defined there. |
| NZT48-ANNEX-003 (War Room Requirements Spec) | Dashboard panel inventory and API endpoint schemas. New panels defined in Section 6.1 extend this spec. |
| NZT48-ANNEX-RC-001 (Risk Constitution) | Circuit breaker thresholds (L1=1.5%, L2=2.5%, L3=4.0%), kill switch authority, position limits. Alert rules A1, A2, A6 reference these thresholds. |
| NZT48-ANNEX-SHO-001 (Self-Healing Ops Spec) | Automated recovery actions. Self-healing actions generate metrics and log entries per this spec. Alert A3 (container restart) may be triggered by self-healing restart actions. |
| NZT48-ANNEX-REGIME-DROUGHT-001 (Regime & Drought Spec) | Drought state definitions (NORMAL, WATCH, DROUGHT, CRITICAL). Metric `nzt48_drought_state` encodes these states. Alert A11 references regime contradictions. |
| NZT48-ANNEX-STARTUP-001 (Startup Readiness Gate Spec) | Startup validation sequence. Metrics collection must be initialised before the startup gate passes. |
| NZT48-ANNEX-SANITY-001 (Sanity Gate Spec) | Pre-signal sanity checks. Gate failures increment `nzt48_scan_signals_vetoed`. |

---

## 11. Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-02-27 | NZT-48 System | Initial specification. Complete metrics catalogue, logging standard, alerting rules (A1-A17), SLO/SLA definitions, dashboard requirements, oncall procedures, incident detection, and acceptance tests. |
