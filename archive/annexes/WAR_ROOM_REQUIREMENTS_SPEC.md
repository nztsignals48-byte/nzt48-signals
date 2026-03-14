# WAR ROOM DASHBOARD -- COMPLETE REQUIREMENTS SPECIFICATION

**Document ID**: NZT48-ANNEX-003
**Version**: 3.0
**Date**: 2026-02-27
**Status**: BINDING -- All War Room panels and API endpoints MUST conform to this specification
**Scope**: Panel inventory (implemented + missing), API endpoint schemas, wiring checks, UX requirements, performance requirements

---

## 1. OBJECTIVE

Define the complete feature checklist for the NZT-48 War Room dashboard (Next.js frontend + FastAPI backend). Enumerate all implemented panels with their API endpoints, specify all missing panels that need new API endpoints, define wiring checks for correctness verification, and set UX and performance requirements.

---

## 2. ARCHITECTURE OVERVIEW

```
+--------------------+       REST/WS       +--------------------+
|   Next.js Frontend | <------------------> |   FastAPI Backend   |
|   (port 3001)      |                      |   (port 8000)      |
+--------------------+                      +--------------------+
        |                                            |
        |  WebSocket /ws/live                        |  SQLite DB
        |  REST /api/*                               |  (data/nzt48.db)
        |                                            |
        +--------------------------------------------+
                                                     |
                                              +--------------+
                                              | NZT-48 Engine|
                                              | (main.py)    |
                                              +--------------+
                                                     |
                                              _internal/push_state
                                              _internal/heartbeat
```

**Authentication**: All state-mutating endpoints require `X-API-Key` header. WebSocket requires `?api_key=` query parameter. Read endpoints are open (localhost only via middleware).

---

## SECTION A: CURRENTLY IMPLEMENTED PANELS (30 panels)

Each panel is listed with its API endpoint, poll interval, and current status.

### A.1 Core Data Panels

| # | Panel Name | API Endpoint | Method | Poll Interval | Status |
|---|-----------|-------------|--------|---------------|--------|
| 1 | Signal Feed | `/api/signals?hours=24&limit=30` | GET | 5s via WS push | WORKING |
| 2 | Virtual Positions | `/api/virtual-positions` | GET | 5s via WS push | WORKING |
| 3 | Market Regime | `/api/regime` | GET | 15s | WORKING |
| 4 | Performance Aggregate | `/api/performance` | GET | 60s | WORKING |
| 5 | Overseer Status | `/api/overseer` | GET | 15s | WORKING |
| 6 | Kelly Criterion | `/api/kelly` | GET | 60s | WORKING |
| 7 | PDT Tracker | `/api/pdt` | GET | 60s | WORKING |
| 8 | Drawdown Status | `/api/drawdown-status` | GET | 15s | WORKING |
| 9 | Missed Trades | `/api/missed-trades?days=7&limit=10` | GET | 60s | WORKING |
| 10 | Trade Autopsies | `/api/trade-autopsies?limit=10` | GET | 60s | WORKING |
| 11 | Firewall Events | `/api/firewall-events?days=7&limit=10` | GET | 60s | WORKING |
| 12 | Regime Transitions | `/api/regime-transitions?days=7&limit=10` | GET | 60s | WORKING |
| 13 | Equity Intraday | `/api/equity-intraday?days=3` | GET | 30s | WORKING |
| 14 | Strategy Daily Stats | `/api/strategy-daily-stats?days=7` | GET | 60s | WORKING |
| 15 | Indicator Scores | `/api/indicator-scores` | GET | 30s | WORKING |
| 16 | Virtual Trades | `/api/virtual-trades?days=7&limit=20` | GET | 60s | WORKING |
| 17 | System Health | `/api/system-health` | GET | 30s | WORKING |
| 18 | Learning State | `/api/learning-state` | GET | 60s | WORKING |
| 19 | Correlation Matrix | `/api/correlation-matrix` | GET | 300s | WORKING |
| 20 | ISA Universe | `/api/isa-universe` | GET | 300s | WORKING |
| 21 | Premarket Briefs | `/api/premarket-briefs?days=3` | GET | 300s | WORKING |
| 22 | Partial Executions | `/api/partial-executions?days=7&limit=10` | GET | 60s | WORKING |
| 23 | Daily Summary | `/api/daily-summary?days=7` | GET | 300s | WORKING |
| 24 | Profit Ladder | `/api/profit-ladder` | GET | 15s | WORKING |
| 25 | Learning Engine | `/api/learning` | GET | 60s | WORKING |
| 26 | Performance by Bot | `/api/performance/by-bot` | GET | 60s | WORKING |

### A.2 Analysis Panels

| # | Panel Name | API Endpoint | Method | Poll Interval | Status |
|---|-----------|-------------|--------|---------------|--------|
| 27 | Market Overview | `/api/analysis/market` | GET | 60s | WORKING |
| 28 | Ticker Analysis | `/api/analysis/tickers` | GET | 60s | WORKING |
| 29 | Scan Analysis | `/api/analysis/scans` | GET | 30s | WORKING |
| 30 | Risk Dashboard | `/api/analysis/risk` | GET | 60s | WORKING |

### A.3 Infrastructure Endpoints (Non-Panel)

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/health` | GET | Health check + engine liveness | WORKING |
| `/api/config` | GET | Non-sensitive system config | WORKING |
| `/api/bots` | GET | Bot allocations + position counts | WORKING |
| `/ws/live` | WS | Real-time signal + position updates | WORKING |
| `/_internal/push_state` | POST | Engine -> API state broadcast | WORKING |
| `/_internal/push_cc_state` | POST | Engine -> CC state broadcast | WORKING |
| `/_internal/heartbeat` | POST | Engine liveness heartbeat | WORKING |
| `/api/kill` | POST | Activate kill switch | WORKING |
| `/api/pause` | POST | Pause strategy or bot | WORKING |
| `/api/resume` | POST | Resume strategy or bot | WORKING |

---

## SECTION B: MISSING PANELS (7 panels -- require new API endpoints)

These panels are called by the frontend (`page.tsx`) but return 404 because the API endpoints do not exist in `dashboard/api.py`. B.7 (Go-Live Gate Page) is a new dedicated full-screen route.

### B.1 SCAN ENGINE HEALTH

**Endpoint**: `GET /api/scan_health`
**Frontend call**: `page.tsx:87` (5-second poll)
**Priority**: P0 (critical -- called every 5 seconds, generates console errors)

**Required Response Schema**:
```json
{
  "state": "RUNNING|PAUSED|KILLED|ERROR|STARTING",
  "tick_count": 14523,
  "engine_runs_today": 847,
  "signals_emitted_today": 12,
  "signals_logged_today": 12,
  "signals_qualified_today": 4,
  "signals_sent_telegram_today": 3,
  "last_success_ts": "2026-02-27T14:30:00.000Z",
  "last_signal_ts": "2026-02-27T14:22:00.000Z",
  "last_signal_ticker": "NVD3.L",
  "last_signal_strategy": "S2",
  "scan_cycle_ms": 847,
  "avg_cycle_ms_60": 912,
  "drought_state": "DROUGHT_NONE",
  "drought_cycle_count": 0,
  "market_regime": "TRENDING_UP_STRONG",
  "vol_regime_majority": "EXPANSION",
  "active_strategies": ["S1", "S2", "S5", "S10", "S11", "S13", "S14", "S15"],
  "dormant_strategies": ["S3"],
  "data_feed_status": "OK|DEGRADED|DOWN",
  "timestamp": "2026-02-27T14:30:01.000Z"
}
```

**Data Source**: Read from `artifacts/system_state.json` + `signals` table + `regime_history` table.

**Error Handling**: If `system_state.json` is stale (> 5 minutes), return `state: "STALE"` and `data_feed_status: "DEGRADED"`.

### B.2 OPPORTUNITY LANE

**Endpoint**: `GET /api/opportunity`
**Frontend call**: `page.tsx:126` (15-second poll)
**Priority**: P0

**Required Response Schema**:
```json
{
  "candidates": [
    {
      "ticker": "NVD3.L",
      "direction": "LONG",
      "feasibility_score": 78.5,
      "decision": "QUALIFIED|BORDERLINE|REJECTED|WATCHING",
      "expected_net_r": 1.8,
      "confidence": 72,
      "strategy": "S2",
      "strategy_name": "Momentum Breakout",
      "entry": 142.50,
      "stop": 140.80,
      "target_1r": 144.20,
      "risk_pct": 0.75,
      "vol_regime": "EXPANSION",
      "rvol": 2.1,
      "rejection_reason": null,
      "gate_passed": "7/7",
      "last_updated": "2026-02-27T14:30:00.000Z"
    }
  ],
  "total_candidates": 5,
  "qualified_count": 2,
  "borderline_count": 1,
  "rejected_count": 2,
  "timestamp": "2026-02-27T14:30:01.000Z"
}
```

**Data Source**: Read from latest engine run results. The engine MUST write opportunity candidates to a `opportunity_candidates` table or `artifacts/opportunity_lane.json` file after each scan cycle.

**Sorting**: Candidates sorted by `feasibility_score` descending.

**Error Handling**: If no candidates available, return `candidates: []` with counts at 0.

### B.3 EXIT SCORES

**Endpoint**: `GET /api/exits`
**Frontend call**: `page.tsx:127` (15-second poll)
**Priority**: P0

**Required Response Schema**:
```json
{
  "positions": [
    {
      "ticker": "QQQ3.L",
      "direction": "LONG",
      "entry_price": 85.20,
      "current_price": 87.10,
      "current_r": 1.2,
      "unrealised_pnl": 190.00,
      "exit_score": 65,
      "sell_intent": "HOLD|REDUCE|EXIT|URGENT_EXIT",
      "kill_conditions": [
        "Approaching T1 (+1.0R) -- consider partial",
        "Vol regime shifting to EXHAUSTION"
      ],
      "ladder_rung": 3,
      "current_stop": 86.00,
      "trail_stop": 86.50,
      "time_in_trade_minutes": 120,
      "mfe": 1.5,
      "mae": -0.2,
      "last_updated": "2026-02-27T14:30:00.000Z"
    }
  ],
  "total_positions": 2,
  "avg_exit_score": 58,
  "positions_at_risk": 0,
  "timestamp": "2026-02-27T14:30:01.000Z"
}
```

**Exit Score Computation**:
- 0-30: HOLD (strong position, let it run)
- 31-50: HOLD (acceptable, monitor)
- 51-70: REDUCE (consider partial exit)
- 71-85: EXIT (close at next opportunity)
- 86-100: URGENT_EXIT (close immediately)

**Kill Conditions**: Array of human-readable strings explaining why exit_score is elevated. Derived from: approaching target, vol regime deterioration, regime change, time decay, drawdown, overseer restrictions.

**Data Source**: `virtual_positions` table (status=OPEN) + real-time price feed + `vol_regimes` table.

### B.4 TELEGRAM DESK TAPE

**Endpoint**: `GET /api/telegram/events`
**Frontend call**: `page.tsx:128` (15-second poll)
**Priority**: P1

**Required Response Schema**:
```json
{
  "events": [
    {
      "id": "evt_001",
      "label": "[SIGNAL]",
      "message_type": "SIGNAL",
      "message_preview": "BUY NVD3.L | S2 Momentum | Conf: 78/100",
      "ticker": "NVD3.L",
      "action": "SENT",
      "timestamp": "2026-02-27T14:22:00.000Z",
      "content_hash": "a1b2c3d4e5f6",
      "dedupe_hit": false,
      "rate_limited": false
    }
  ],
  "dedupe_stats": {
    "total_attempted": 150,
    "total_sent": 48,
    "total_deduped": 87,
    "total_rate_limited": 12,
    "total_errors": 3,
    "dedupe_rate_pct": 58.0
  },
  "rate_limit_status": {
    "per_minute_remaining": 3,
    "per_hour_remaining": 22,
    "spam_paused": false,
    "spam_pause_until": null
  },
  "quiet_period_active": false,
  "timestamp": "2026-02-27T14:30:01.000Z"
}
```

**Data Source**: Read from `data/telegram_debug.jsonl` (last 100 entries) + in-memory rate limiter state + dedupe state.

**Error Handling**: If `telegram_debug.jsonl` does not exist or is empty, return `events: []` with zeroed stats.

### B.5 CONSISTENCY CHECK

**Endpoint**: `GET /api/consistency`
**Frontend call**: `page.tsx:129` (15-second poll)
**Priority**: P1

**Required Response Schema**:
```json
{
  "consistent": true,
  "checks": [
    {
      "check_name": "regime_taxonomy_alignment",
      "status": "PASS|WARN|FAIL",
      "message": "Market regime and vol regime are consistent",
      "details": {
        "market_regime": "TRENDING_UP_STRONG",
        "vol_regime_majority": "EXPANSION",
        "rule_triggered": null
      }
    },
    {
      "check_name": "drought_regime_consistency",
      "status": "PASS",
      "message": "No drought-regime contradiction detected",
      "details": {
        "drought_state": "DROUGHT_NONE",
        "market_regime": "TRENDING_UP_STRONG"
      }
    },
    {
      "check_name": "signal_data_freshness",
      "status": "PASS",
      "message": "All data feeds within freshness threshold (< 5 min)",
      "details": {
        "oldest_feed": "VIX",
        "oldest_feed_age_seconds": 45
      }
    },
    {
      "check_name": "position_stop_integrity",
      "status": "PASS",
      "message": "All open positions have valid stops",
      "details": {
        "positions_checked": 3,
        "positions_without_stops": 0
      }
    },
    {
      "check_name": "kill_switch_state",
      "status": "PASS",
      "message": "Kill switch not active",
      "details": {
        "kill_file_exists": false
      }
    },
    {
      "check_name": "pdf_regime_match",
      "status": "PASS",
      "message": "Latest PDF regime matches current system regime",
      "details": {
        "pdf_regime": "TRENDING_UP_STRONG",
        "system_regime": "TRENDING_UP_STRONG"
      }
    }
  ],
  "warnings": [],
  "errors": [],
  "timestamp": "2026-02-27T14:30:01.000Z"
}
```

**Consistency Checks (6 mandatory)**:
1. `regime_taxonomy_alignment` -- Cross-layer regime consistency (Section 4 of REGIME_DROUGHT_SPEC.md).
2. `drought_regime_consistency` -- Drought-regime contradiction (Section 6 of REGIME_DROUGHT_SPEC.md).
3. `signal_data_freshness` -- All data feeds within 5-minute staleness threshold.
4. `position_stop_integrity` -- All open positions have non-zero, non-null stops.
5. `kill_switch_state` -- Kill switch file presence matches expected state.
6. `pdf_regime_match` -- Latest generated PDF's regime label matches current system regime.

**Overall `consistent` flag**: `true` if ALL checks are PASS. `false` if any check is WARN or FAIL.

### B.6 OPERATOR COPILOT

**Endpoint**: `POST /api/copilot/query`
**Frontend call**: `page.tsx:218` (on-demand, user-triggered)
**Priority**: P2

**Request Schema**:
```json
{
  "query": "Why has there been no signal for 30 minutes?"
}
```

**Required Response Schema**:
```json
{
  "answer": "The system has been in DROUGHT_ACTIVE state for 25 cycles. Market regime is RANGE_BOUND which typically produces fewer signals. The qualification gates are rejecting candidates due to low confidence (< 60). The closest candidate was NVD3.L (S2, confidence 57, rejected at gate 5).",
  "confidence": 0.85,
  "actions": [
    {
      "label": "View rejected candidates",
      "endpoint": "/api/opportunity",
      "method": "GET"
    },
    {
      "label": "Lower confidence threshold to 55",
      "endpoint": "/api/config/override",
      "method": "POST",
      "body": {"key": "qualification.min_confidence", "value": 55}
    }
  ],
  "warnings": [
    "Lowering confidence threshold may admit lower-quality signals"
  ],
  "data_sources_consulted": [
    "system_state.json",
    "signals table (last 50)",
    "regime_history (last 10)",
    "opportunity_candidates"
  ],
  "timestamp": "2026-02-27T14:30:01.000Z"
}
```

**Implementation Options** (in priority order):
1. Rule-based pattern matching on common questions (drought, regime, performance, positions).
2. Template-based answers using system state data.
3. Future: LLM integration via Gemini Cloud Function (out of scope for V1).

**Authentication**: Requires `X-API-Key` header (state-mutating actions suggested in response).

### B.7 GO-LIVE GATE PAGE

**Endpoint**: `GET /api/go-live-gate`
**Frontend**: Dedicated route `/gate` (full-screen, no other panels)
**Priority**: P0 (must be operational before any live trading gate)

**Required Response Schema**:
```json
{
  "go_live": true,
  "gate_time": "2026-02-27T07:45:00.000Z",
  "checks": [
    {
      "name": "system_state",
      "label": "SystemState",
      "status": "PASS",
      "detail": "Engine running, mode=PAPER, no errors",
      "source": "artifacts/system_state.json",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "sanity_gate",
      "label": "SanityGate",
      "status": "PASS",
      "detail": "All magnitude + score filters active",
      "source": "main.py sanity gate module",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "data_health",
      "label": "DataHealth",
      "status": "PASS",
      "detail": "Coverage 94.2% (threshold: ≥80%)",
      "source": "provenance engine",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "telegram_tape",
      "label": "Telegram Tape",
      "status": "PASS",
      "detail": "0 invalid-score events in last 60 minutes",
      "source": "telegram_debug.jsonl",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "dedupe_active",
      "label": "Dedupe",
      "status": "PASS",
      "detail": "Persistent dedupe active, 847 hashes in window",
      "source": "telegram_state DB table",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "war_room_qa",
      "label": "War Room QA",
      "status": "PASS",
      "detail": "Last Playwright run: PASS (2026-02-27T06:00:00Z)",
      "source": "artifacts/wiring_check_results.json",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "pdf_audit",
      "label": "PDF Audit",
      "status": "PASS",
      "detail": "Last PDF QA: PASS (PDF1 + PDF2)",
      "source": "data/pdf_qa_log.jsonl",
      "last_checked": "2026-02-27T07:45:00.000Z"
    },
    {
      "name": "scan_sla",
      "label": "Scan SLA",
      "status": "PASS",
      "detail": "Tick loop alive, 847 engine runs today, P95 cycle < 45s",
      "source": "artifacts/scan_health.json",
      "last_checked": "2026-02-27T07:45:00.000Z"
    }
  ],
  "pass_count": 8,
  "fail_count": 0,
  "warn_count": 0,
  "timestamp": "2026-02-27T07:45:01.000Z"
}
```

**Gate Logic**:
- `go_live = true` ONLY if ALL 8 checks are PASS
- `go_live = false` if ANY check is WARN or FAIL
- Each check runs independently -- one failure does not prevent other checks from reporting
- Gate page auto-refreshes every 30 seconds

**8 Mandatory Checks (BINDING)**:

| # | Check Name | PASS Condition | WARN Condition | FAIL Condition |
|---|-----------|---------------|---------------|---------------|
| 1 | system_state | Engine running, no crashes in last session | Engine running but DEGRADED status | Engine DOWN or STALE (>5min heartbeat) |
| 2 | sanity_gate | All magnitude + score filters active (feature flag ON) | Feature flag OFF but no bad signals detected | Feature flag OFF and bad signals in last 24h |
| 3 | data_health | Coverage ≥80% for all CORE tickers | Coverage 50-79% for any CORE ticker | Coverage <50% for any CORE ticker |
| 4 | telegram_tape | 0 invalid-score events in last 60 minutes | 1-2 invalid-score events in last 60 min | >2 invalid-score events in last 60 min |
| 5 | dedupe_active | Persistent dedupe active and DB accessible | Dedupe active but DB not writable (in-memory fallback) | Dedupe disabled or not initialised |
| 6 | war_room_qa | Last Playwright run PASS, all endpoints 200, <24h ago | Last run PASS but >24h ago | Last run FAIL or no run recorded |
| 7 | pdf_audit | Last PDF QA PASS for both PDF1 and PDF2 | Last QA had WARN (non-critical check fail) | Last QA FAIL (critical check fail) |
| 8 | scan_sla | Tick loop alive, engine_runs > 0, P95 cycle < 45s | P95 cycle 45-55s (approaching budget) | Tick loop dead OR engine_runs = 0 OR P95 > 55s |

**Data Sources per Check**:
- system_state: `artifacts/system_state.json` (parsed on each refresh)
- sanity_gate: Feature flag state from `settings.yaml` + `system_state.json` blocked_count
- data_health: Provenance engine coverage report (or `system_state.json` data_reliability field)
- telegram_tape: Scan `telegram_debug.jsonl` for events with `score=0` or `score=None` in last 60min
- dedupe_active: Query `telegram_state` DB table; check row count > 0 and last write < 5min
- war_room_qa: Read `artifacts/wiring_check_results.json` for last run timestamp and result
- pdf_audit: Read last entry from `data/pdf_qa_log.jsonl`
- scan_sla: Read `artifacts/scan_health.json` for tick_count, engine_runs, and cycle timing

**Self-Validation (Resilience to Code Changes)**:
The gate page MUST validate its own integrity on every refresh:
1. Verify all 8 check functions are registered (not silently removed)
2. Verify each data source file exists and is parseable
3. If a check function throws an exception, that check reports FAIL with the exception message (never silently passes)
4. If a data source is missing, that check reports FAIL with "source not found" (never silently passes)
5. Log every gate evaluation to `data/go_live_gate_log.jsonl` with full check results

**Error Handling**: If the gate endpoint itself crashes, return HTTP 500 with `{"go_live": false, "error": "gate endpoint failure"}`. The frontend must show a RED "GATE ERROR" screen -- never default to green.

---

## SECTION C: WIRING CHECKS

For each panel, verify the following checklist. All 37 panels (30 existing + 7 new) MUST pass every check.

### C.1 Wiring Check Matrix

| Check ID | Check Description | How to Verify |
|----------|------------------|---------------|
| W1 | API endpoint exists in `api.py` | `grep -c "endpoint_path" dashboard/api.py` returns >= 1 |
| W2 | API endpoint returns correct HTTP status (200 for success, appropriate error codes) | `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/{endpoint}` returns 200 |
| W3 | Response matches documented schema (all required fields present) | JSON schema validation against spec |
| W4 | Response handles empty data gracefully (no crashes, returns empty arrays/defaults) | Test with empty DB tables |
| W5 | Response handles DB connection failure gracefully | Test with locked/missing DB |
| W6 | Frontend calls the correct endpoint path | `grep "endpoint_path" page.tsx` matches |
| W7 | Frontend poll interval matches spec | Code review of `setInterval`/`useEffect` deps |
| W8 | Frontend handles null/undefined responses without crash | Test by returning `null` from API |
| W9 | Frontend handles network timeout (> 5s) gracefully | Simulate slow response |
| W10 | API response time < 100ms (see Section E) | Measured via middleware timing header |

### C.2 Per-Panel Wiring Status

| Panel # | Panel Name | W1 | W2 | W3 | W4 | W5 | W6 | W7 | W8 | W9 | W10 |
|---------|-----------|----|----|----|----|----|----|----|----|----|----|
| 1 | Signal Feed | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 2 | Virtual Positions | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 3 | Market Regime | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 4 | Performance | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 5 | Overseer | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 6 | Kelly | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 7 | PDT | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 8-30 | (remaining) | OK | OK | OK | OK | OK | OK | OK | ? | ? | OK |
| 31 | Scan Health | MISSING | 404 | N/A | N/A | N/A | OK | OK | ? | ? | N/A |
| 32 | Opportunity Lane | MISSING | 404 | N/A | N/A | N/A | OK | OK | ? | ? | N/A |
| 33 | Exit Scores | MISSING | 404 | N/A | N/A | N/A | OK | OK | ? | ? | N/A |
| 34 | Telegram Desk | MISSING | 404 | N/A | N/A | N/A | OK | OK | ? | ? | N/A |
| 35 | Consistency | MISSING | 404 | N/A | N/A | N/A | OK | OK | ? | ? | N/A |
| 36 | Copilot | MISSING | 404 | N/A | N/A | N/A | OK | OK | ? | ? | N/A |
| 37 | Go-Live Gate | MISSING | 404 | N/A | N/A | N/A | NEW | NEW | ? | ? | N/A |

**Note**: `?` indicates untested. These checks require manual verification or automated test suite.

### C.3 Mandatory Wiring Fix: Frontend Null Safety

The following frontend locations have identified null-check gaps (from FORENSICS_MAP Section 6.3):

| File | Line(s) | Issue | Required Fix |
|------|---------|-------|-------------|
| `page.tsx:92` | `setPerformance(perf.aggregate)` | Assumes `aggregate` key exists | Add `perf?.aggregate ?? defaultPerformance` |
| `page.tsx:982` | `tp.rolling_60d_wr * 100` | Assumes number, not null | Add `(tp.rolling_60d_wr ?? 0) * 100` |
| `page.tsx:1029-1035` | Sort by count | NaN if count null | Add `(a.count ?? 0) - (b.count ?? 0)` |

---

## SECTION D: UX REQUIREMENTS

### D.1 Global Status Indicators

Every War Room page MUST display the following persistent indicators in the header bar:

| Indicator | Position | Data Source | Update Interval |
|-----------|----------|-------------|-----------------|
| Engine Connection Status | Top-left | `/api/health` `engine` field | 5s |
| WebSocket Connection Status | Top-left (next to engine) | WS `onopen`/`onclose` events | Real-time |
| Market Regime Badge | Top-center | `/api/regime` `current.state` | 15s via WS push |
| System Mode Badge | Top-center (next to regime) | `/api/config` `system_mode` | 60s |
| Drought State Badge | Top-center (next to mode) | `/api/scan_health` `drought_state` | 5s |
| Kill Switch Status | Top-right | `/api/health` + kill file check | 5s |
| Clock (UTC + UK) | Top-right | Client-side | 1s |

### D.2 Connection Status States

| State | Display | Colour | Icon |
|-------|---------|--------|------|
| Connected (engine + WS both alive) | "CONNECTED" | Green (#00E676) | Filled circle |
| Engine Stale (heartbeat > 120s) | "ENGINE STALE" | Orange (#FFA000) | Warning triangle |
| WS Disconnected | "WS DISCONNECTED" | Red (#FF5252) | Broken chain |
| API Unreachable | "API DOWN" | Red (#FF5252) | X circle |
| Kill Switch Active | "KILLED" | Red, pulsing | Skull |

### D.3 Regime Badge States

| Regime | Colour | Badge Text |
|--------|--------|------------|
| TRENDING_UP_STRONG | Green (#00E676) | "TREND UP (STRONG)" |
| TRENDING_UP_MOD | Light Green (#69F0AE) | "TREND UP (MOD)" |
| TRENDING_DOWN_STRONG | Red (#FF5252) | "TREND DOWN (STRONG)" |
| TRENDING_DOWN_MOD | Light Red (#FF8A80) | "TREND DOWN (MOD)" |
| RANGE_BOUND | Grey (#9E9E9E) | "RANGE" |
| HIGH_VOLATILITY | Orange (#FFA000) | "HIGH VOL" |
| RISK_OFF | Red, pulsing (#FF5252) | "RISK OFF" |
| SHOCK | Red, pulsing, animated (#FF1744) | "SHOCK" |

### D.4 Data Freshness Indicators

Each panel MUST display a freshness indicator showing when the data was last updated:

| Freshness | Display | Colour |
|-----------|---------|--------|
| < 10 seconds ago | No indicator (fresh) | Default |
| 10-60 seconds ago | "10s ago", "45s ago" | Yellow |
| 60-300 seconds ago | "1m ago", "3m ago" | Orange |
| > 300 seconds ago | "STALE (5m+)" | Red, pulsing |

### D.5 Panel Error States

When a panel fails to load data, it MUST display:
1. Grey overlay with "Error loading data" message.
2. Last known data underneath (greyed out).
3. Retry button.
4. Error details expandable (API error message, status code).

### D.6 Responsive Layout

| Screen Width | Layout |
|-------------|--------|
| >= 1920px (4K) | 4-column grid, all panels visible |
| >= 1440px (QHD) | 3-column grid, all panels visible |
| >= 1024px (HD) | 2-column grid, scroll for lower panels |
| < 1024px | Single column, panels stacked |

### D.7 Go-Live Gate Page

The Go-Live Gate Page is a dedicated full-screen view at route `/gate`.

**Layout**:
- Full-screen, single-column
- Large "GO" (green) or "NO-GO" (red) indicator at top, filling 30% of viewport height
- 8 check cards below, each showing: check name, status badge (PASS/WARN/FAIL), detail text, last-checked timestamp
- Auto-refresh countdown timer (30s) in footer
- Manual "Refresh Now" button

**Colour Coding**:

| Status | Background | Text | Icon |
|--------|-----------|------|------|
| PASS | Green (#00E676) | White | Checkmark |
| WARN | Orange (#FFA000) | Black | Warning triangle |
| FAIL | Red (#FF5252) | White | X circle |

**Top Indicator**:
- GO: Large green circle with "GO" text, pulsing gently
- NO-GO: Large red circle with "NO-GO" text, pulsing urgently
- If ANY check is FAIL: show NO-GO
- If ANY check is WARN (and none FAIL): show "CONDITIONAL" in orange

**Interaction**:
- Click any check card to expand detail panel showing: raw source data excerpt, last 5 state transitions, link to source artifact
- "Print Report" button generates a single-page PDF snapshot of the gate state for PM/IC sign-off
- Gate history: collapsible section showing last 10 evaluations with timestamps

---

## SECTION E: PERFORMANCE REQUIREMENTS

### E.1 API Response Time Targets

| Endpoint Category | Max Response Time | 95th Percentile Target |
|------------------|-------------------|----------------------|
| Simple DB reads (signals, positions, regime) | 50ms | 30ms |
| Aggregate queries (performance, stats) | 100ms | 70ms |
| yfinance-dependent (analysis/market, tickers) | 200ms (cached) | 100ms (cached) |
| Complex computation (consistency, exits) | 150ms | 100ms |
| Copilot query (POST) | 2000ms | 1000ms |

### E.2 Measurement

All API endpoints MUST include a timing header:
```
X-Response-Time: 45ms
```

Implementation: Add timing middleware to FastAPI:
```python
class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed:.0f}ms"
        return response
```

### E.3 WebSocket Performance

| Metric | Target |
|--------|--------|
| Max broadcast rate | 2 updates/second per client |
| Max payload size per message | 64 KB |
| Connection timeout | 30 seconds of no ping/pong |
| Max concurrent connections | 10 |
| Reconnect backoff | 1s, 2s, 4s, 8s, max 30s |

### E.4 Frontend Performance

| Metric | Target |
|--------|--------|
| Initial page load (all panels) | < 3 seconds |
| Panel data refresh (single panel) | < 500ms visual update |
| Memory usage (Chrome tab) | < 200 MB after 1 hour |
| CPU usage (idle, all panels updating) | < 10% of single core |
| No memory leaks after 8 hours | < 50 MB growth |

### E.5 Graceful Degradation

When network connectivity is lost:
1. All panels show "STALE" freshness indicator.
2. Last known data remains visible (not cleared).
3. WebSocket auto-reconnects with exponential backoff (1s, 2s, 4s, 8s, max 30s).
4. A banner appears at top: "Network disconnected -- showing cached data. Reconnecting..."
5. On reconnect, all panels refresh immediately (burst fetch).

When engine is down but API is up:
1. Engine status shows "ENGINE STALE" (orange).
2. All DB-backed panels continue to work with last known data.
3. yfinance-backed panels continue with cached data.
4. Scan Health panel shows `state: "STALE"`.
5. No new signals will appear (expected).

---

## 3. FAILURE MODES

| # | Failure Mode | Detection | Impact | Mitigation |
|---|-------------|-----------|--------|------------|
| F1 | API server crashes | Frontend health check fails | All panels show error state | Auto-restart via Docker. Frontend shows "API DOWN" banner. |
| F2 | DB locked (SQLite write contention) | `SQLITE_BUSY` error on read | Stale data in panels | WAL mode (already enabled). Read timeout = 5s with retry. |
| F3 | yfinance rate limited | HTTP 429 from Yahoo | Market data panels stale | 60s cache TTL (already implemented). Stale cache fallback. |
| F4 | WebSocket floods frontend | Memory spike, lag | UI freeze | Max 2 broadcasts/sec. Client-side throttle. |
| F5 | Frontend memory leak | Chrome task manager | Slow/unresponsive dashboard | useEffect cleanup. Proper interval clearing. Profile with DevTools. |
| F6 | Missing API endpoint (404) | Console errors, blank panel | 7 panels broken | Implement all Section B endpoints. |
| F7 | Null data in API response | Frontend crashes/NaN | Broken panel display | Fix all null-check gaps (Section C.3). |
| F8 | CORS rejection | Browser console error | No data loads | Verify CORS origins include dashboard URL. |

---

## 4. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| Panel shows "Error loading data" | Check API health: `curl http://localhost:8000/api/health`. Check API logs: `docker logs nzt48 --tail 50`. |
| "ENGINE STALE" indicator | SSH to server. Check engine process: `docker ps`. Restart if needed: `docker-compose restart nzt48`. |
| "WS DISCONNECTED" indicator | Refresh browser. Check if API is accessible. Check firewall rules. |
| Kill switch activated accidentally | Remove kill file: `rm data/KILL_SWITCH`. Restart engine. |
| Dashboard shows stale prices | Check yfinance: market may be closed. Cache TTL is 60s. Wait for next refresh. |
| High memory usage in browser | Close and reopen dashboard tab. Report issue if persistent. |

---

## 5. ACCEPTANCE TESTS

### 5.1 API Endpoint Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T1 | `GET /api/scan_health` | Returns 200 with all required fields | Schema validation passes |
| T2 | `GET /api/opportunity` | Returns 200 with candidates array | Schema validation passes |
| T3 | `GET /api/exits` | Returns 200 with positions array | Schema validation passes |
| T4 | `GET /api/telegram/events` | Returns 200 with events array + stats | Schema validation passes |
| T5 | `GET /api/consistency` | Returns 200 with checks array | Schema validation passes |
| T6 | `POST /api/copilot/query` with body | Returns 200 with answer | Schema validation passes |
| T7 | All 37 endpoints return 200 when DB is populated | No 404 or 500 errors | All return 200 |
| T8 | All 37 endpoints return valid JSON when DB is empty | Empty arrays, default values | No crashes |
| T9 | All 37 endpoints respond within performance targets | X-Response-Time header within spec | All under target |
| T21 | `GET /api/go-live-gate` | Returns 200 with all 8 checks | Schema validation passes, all required checks present |
| T22 | `GET /api/go-live-gate` with one source missing | Returns 200 with that check as FAIL | go_live=false, failed check visible |
| T23 | Go-Live Gate page renders in browser | Route `/gate` shows GO/NO-GO indicator | Visual confirmation |

### 5.2 Frontend Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T10 | Load War Room with all 37 panels | All panels render without console errors | Zero console.error |
| T11 | Disconnect API (kill API process) | "API DOWN" banner appears, panels show last data | No crashes |
| T12 | Stop engine (but keep API running) | "ENGINE STALE" after 120s, panels still show DB data | No crashes |
| T13 | Simulate WS disconnect | "WS DISCONNECTED", auto-reconnect within 30s | Reconnect successful |
| T14 | Run dashboard for 1 hour | Memory < 200 MB, no performance degradation | Chrome DevTools verification |
| T15 | Trigger regime change via DB injection | Regime badge updates within 15s | Badge text and colour match spec |
| T16 | Trigger kill switch via `/api/kill` | Kill indicator appears, all signal panels stop updating | Kill state reflected everywhere |
| T24 | Go-Live Gate auto-refreshes every 30s | Data updates without manual refresh | Timer counts down and re-fetches |

### 5.3 Wiring Integration Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T17 | Run full wiring check (C.1) for all 37 panels | All checks pass | Zero failures |
| T18 | Generate a signal and verify it appears in Signal Feed panel within 5s | Signal visible in panel | Visual confirmation |
| T19 | Open a position and verify it appears in Virtual Positions panel within 5s | Position visible | Visual confirmation |
| T20 | Generate a Telegram message and verify it appears in Telegram Desk panel within 15s | Event visible with correct label | Label matches |
| T25 | Force one check to FAIL, verify gate shows NO-GO | Toggle sanity_gate_v2 flag to false | Gate page shows red NO-GO, specific check shows FAIL |

---

## 6. PROOF ARTIFACTS

| # | Artifact | Location | Description |
|---|----------|----------|-------------|
| A1 | API schema validation results | `artifacts/api_schema_test.json` | JSON output from validating all 37 endpoints against their schemas |
| A2 | Wiring check results | `artifacts/wiring_check_results.json` | Pass/fail for each panel, each check |
| A3 | Performance benchmark | `artifacts/api_performance_bench.json` | p50/p95/p99 response times for each endpoint |
| A4 | Frontend error log (1 hour run) | `artifacts/frontend_error_log.txt` | Console output from 1-hour dashboard session |
| A5 | Screenshot gallery | `artifacts/war_room_screenshots/` | Screenshot of each panel with data, each error state, each indicator state |
| A6 | Missing endpoint implementation PRs | Git commit hashes | Commits implementing B.1-B.7 |
| A7 | Go-Live Gate evaluation log | `data/go_live_gate_log.jsonl` | Timestamped log of every gate evaluation with all 8 check results |
| A8 | Go-Live Gate screenshot (PASS state) | `artifacts/war_room_screenshots/gate_pass.png` | Screenshot showing green GO indicator |
| A9 | Go-Live Gate screenshot (FAIL state) | `artifacts/war_room_screenshots/gate_fail.png` | Screenshot showing red NO-GO indicator |

---

## SECTION F: MANDATORY WIRING PROOF PACK

Before any War Room workstream (W7) can be marked COMPLETE, the following proof pack MUST be assembled and archived. This pack serves as PM/IC evidence that all panels are wired and functional.

### F.1 Proof Pack Contents (ALL REQUIRED)

| # | Artifact | Format | Location | Pass Criteria |
|---|----------|--------|----------|--------------|
| P1 | All-Endpoints-200 Report | JSON | `artifacts/wiring_proof/all_endpoints_200.json` | Every one of 36 API endpoints returns HTTP 200 with valid JSON body |
| P2 | API Schema Validation | JSON | `artifacts/wiring_proof/schema_validation.json` | Every endpoint response matches its documented schema (Section A + B) |
| P3 | Playwright Screenshot Pack | PNG files | `artifacts/wiring_proof/screenshots/` | One screenshot per panel (36 minimum), each showing data (not error/loading state) |
| P4 | Playwright Console Log | TXT | `artifacts/wiring_proof/console_errors.txt` | Zero `console.error` entries across full War Room navigation |
| P5 | Playwright Run Summary | JSON | `artifacts/wiring_proof/playwright_results.json` | All T-PW-001 through T-PW-020 tests PASS |
| P6 | Performance Benchmark | JSON | `artifacts/wiring_proof/performance_bench.json` | All endpoints within response time targets (Section E.1) |
| P7 | WebSocket Connectivity | JSON | `artifacts/wiring_proof/websocket_test.json` | WS connects, receives data, survives disconnect/reconnect |
| P8 | Go-Live Gate PASS Screenshot | PNG | `artifacts/wiring_proof/screenshots/gate_pass.png` | Go-Live Gate page showing green GO with all 8 checks PASS |

### F.2 Proof Pack Generation Process

1. **Prerequisites**: All 36 API endpoints implemented and returning data. Docker containers running on EC2.
2. **Step 1**: Run `curl` against all 36 endpoints → generate P1 (all_endpoints_200.json)
3. **Step 2**: Run JSON schema validator against all responses → generate P2 (schema_validation.json)
4. **Step 3**: Run Playwright test suite → generates P3 (screenshots), P4 (console log), P5 (results)
5. **Step 4**: Run performance benchmark (100 requests per endpoint) → generate P6
6. **Step 5**: Run WebSocket test (connect, receive 3 updates, disconnect, reconnect) → generate P7
7. **Step 6**: Navigate to `/gate` route → screenshot → generate P8
8. **Step 7**: Archive all artifacts with timestamp: `artifacts/wiring_proof/proof_pack_YYYYMMDD_HHMM/`

### F.3 Proof Pack Validation Rules

- **Pack is INVALID if any artifact is missing**
- **Pack is INVALID if any P1 entry shows non-200 status**
- **Pack is INVALID if any P2 schema validation fails**
- **Pack is INVALID if P4 contains any `console.error` entries**
- **Pack is INVALID if any P5 test result is FAIL**
- **Pack EXPIRES after 7 days** — must be regenerated if War Room code changes
- **Pack must be regenerated after ANY change to**: api.py, page.tsx, analysis/page.tsx, lib/api.ts

### F.4 PM/IC Review Checklist

The PM/IC reviewer should verify:
- [ ] `all_endpoints_200.json` shows exactly 36 endpoints, all status 200
- [ ] Screenshot pack contains 36+ screenshots, each showing real data
- [ ] Console log is empty (zero errors)
- [ ] Performance benchmark shows all P95 < 200ms
- [ ] Go-Live Gate screenshot shows all 8 checks PASS
- [ ] Proof pack timestamp is within 7 days of review date

---

## REVISION HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | NZT-48 Spec Engine | Initial binding specification |
| 2.0 | 2026-02-27 | NZT-48 Spec Engine | Added B.7 Go-Live Gate Page (API + 8 mandatory checks + self-validation), D.7 Go-Live Gate Page UX (layout, colour coding, interaction), acceptance tests T21-T25, proof artifacts A7-A9. Section B updated from 6 to 7 panels. |
| 3.0 | 2026-02-27 | NZT-48 Spec Engine | Added Section F: Mandatory Wiring Proof Pack (P1-P8 artifacts, generation process, validation rules, PM/IC review checklist) |
| 4.0 | 2026-02-27 | NZT-48 Spec Engine | Added Section G: System Wiring Panel (W13 Always-Wired addendum) |

---

## G. SYSTEM WIRING PANEL (W13 ADDENDUM)

**Added by**: `docs/ADDENDUM_ALWAYS_WIRED_110.md` v1.0
**Requirement**: REQ-065, REQ-072

### G.1 Purpose

The System Wiring panel provides fund-manager-level visibility into whether all system components are connected and healthy. It is the operational equivalent of a cockpit "master caution" display — one glance tells the operator if anything is disconnected.

### G.2 Panel Layout

The panel displays **7 indicators** in a horizontal bar at the top of the War Room dashboard:

| # | Indicator | Source | Green | Amber | Red |
|---|-----------|--------|-------|-------|-----|
| 1 | **DataHub** | Provenance engine / provider health | All providers responding; coverage ≥80% | 1-2 providers down; coverage 60-80% | ≥3 providers down; coverage <60% |
| 2 | **Engine** | `artifacts/system_state.json` last_tick | Last tick within 120s | Last tick 120-300s ago | Last tick >300s ago or engine_runs=0 |
| 3 | **Artifacts** | Integrity monitor artifact freshness | All critical artifacts within TTL | 1 artifact stale | ≥2 artifacts stale or any missing |
| 4 | **Telegram** | `telegram_debug.jsonl` stats | 0 invalid-score events in 60min; send success | 1-2 suppressed; rate limited | >2 invalid events; delivery failure |
| 5 | **PDF** | `data/pdf_qa_log.jsonl` | Last QA PASS for both PDF1+PDF2 | Last QA WARN (non-critical) | Last QA FAIL (critical check) |
| 6 | **Learning** | Edge ledger + outcome resolver status | Outcome resolver running; edge ledger accessible | Outcome backlog >10 | Resolver stopped; ledger inaccessible |
| 7 | **Scheduler** | APScheduler job listing | All expected jobs registered | 1 job missing | ≥2 jobs missing or scheduler stopped |

### G.3 Additional Elements

- **Last success timestamp** under each indicator (e.g., "2 min ago")
- **Reason code** on hover for Amber/Red indicators
- **"First Thing" readiness checklist** displayed prominently at 06:55 and 13:25 UK (REQ-072):
  - Shows the 8 startup gate checks with PASS/FAIL status
  - Auto-hides after 15 minutes if all PASS
  - Persists if any check is FAIL/DEGRADED

### G.4 API Endpoint

**`GET /api/system-wiring`**

```json
{
  "indicators": {
    "datahub": {"status": "green", "detail": "5/5 providers OK", "last_success": "2026-02-27T07:01:23Z"},
    "engine": {"status": "green", "detail": "tick 43s ago", "last_success": "2026-02-27T07:01:17Z"},
    "artifacts": {"status": "green", "detail": "all within TTL", "last_success": "2026-02-27T07:01:17Z"},
    "telegram": {"status": "green", "detail": "0 suppressed/60min", "last_success": "2026-02-27T07:00:45Z"},
    "pdf": {"status": "green", "detail": "QA PASS both", "last_success": "2026-02-27T06:55:00Z"},
    "learning": {"status": "amber", "detail": "backlog: 3", "last_success": "2026-02-27T06:58:00Z"},
    "scheduler": {"status": "green", "detail": "all 5 jobs OK", "last_success": "2026-02-27T07:01:00Z"}
  },
  "overall": "amber",
  "readiness_gate": {
    "status": "READY",
    "checks": [...],
    "show_checklist": false
  },
  "as_of": "2026-02-27T07:01:23Z"
}
```

### G.5 Acceptance Tests

| Test ID | Description | Expected |
|---------|-------------|----------|
| T-PW-021 | Panel renders with 7 indicators | All 7 visible with correct labels |
| T-PW-022 | Indicators reflect actual state (all green) | Green when all systems healthy |
| T-PW-023 | Indicator turns red on failure | Stop engine → Engine indicator red within 60s |
| T-PW-024 | Readiness checklist at session times | Visible at 06:55 UK; auto-hides after 15 min if all PASS |
