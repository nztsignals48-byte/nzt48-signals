# NZT-48 IC-Level Acceptance Tests -- Master

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-ATM-001            |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- IC/PM Sign-Off     |
| Related         | TEST_PLAN.md (242 detailed tests) |

---

## 1. PURPOSE

TEST_PLAN.md contains 242 detailed tests across unit, integration, regression, Playwright, PDF QA, performance, and gate categories. This document extracts the **IC-level subset**: the tests that the Investment Committee and Portfolio Manager would verify before approving the system for each gate transition (Paper Stable, Paper Ready, Limited Live).

Each test includes a proof artifact requirement so that sign-off is evidence-based, not assertion-based.

---

## 2. IC-LEVEL TEST CATEGORIES

| # | Category | Test Count | Scope |
|---|----------|-----------|-------|
| 1 | Signal Pipeline | 5 | End-to-end signal generation, scoring, qualification |
| 2 | Execution Chain | 4 | Virtual trader opens/closes positions correctly |
| 3 | Risk Controls | 5 | Circuit breakers, kill switch, position limits, drawdown |
| 4 | Data Quality | 4 | Feed freshness, staleness detection, OHLC integrity |
| 5 | Output Delivery | 4 | Telegram, PDF, War Room output correctness |
| 6 | Monitoring | 3 | War Room, alerts, system health visibility |
| 7 | Rollback | 3 | Feature flag rollback, LKG restore, kill switch persistence |
| 8 | Security | 3 | API authentication, kill switch persistence, no data leakage |
| 9 | Performance | 4 | Scan SLA, API latency, memory bounds, uptime |
| 10 | Go-Live Gate | 3 | 8-check gate, self-validation, evidence pack |
| **Total** | | **38** | |

---

## 3. PER-CATEGORY TESTS

### 3.1 Signal Pipeline

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-SP-001 | End-to-end: qualifying signal passes all 7 gates and reaches Telegram | Signal with score>=10, confidence>=60, fresh data, regime-consistent reaches Telegram mock within 30s of scan cycle | `artifacts/ic_tests/sp001_signal_e2e.json` (signal payload + gate verdicts + Telegram confirmation) | [ ] |
| IC-SP-002 | Score=0 signal is blocked before any output channel | Signal with score=0 never reaches Telegram, PDF, or War Room regardless of confidence | `artifacts/ic_tests/sp002_score_zero_block.json` (gate log showing BLOCK at score gate) | [ ] |
| IC-SP-003 | Impossible magnitude (>30% session return) held for manual review | Signal with +35% return flagged UNVERIFIED and held; operator notified | `artifacts/ic_tests/sp003_magnitude_hold.json` (hold event + operator notification log) | [ ] |
| IC-SP-004 | Regime mismatch between signal generation and send time blocks delivery | Signal generated under TRENDING_UP but regime shifts to VOLATILE before send; signal blocked | `artifacts/ic_tests/sp004_regime_mismatch.json` (consistency gate BLOCK log) | [ ] |
| IC-SP-005 | Stale data (>TTL) blocks signal delivery | Signal with price data older than 120s during market hours is blocked | `artifacts/ic_tests/sp005_staleness_block.json` (staleness gate BLOCK with field age) | [ ] |

### 3.2 Execution Chain

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-EX-001 | Virtual trader opens position from qualified signal | Position created in `virtual_positions` table with correct entry price, stop, target | `artifacts/ic_tests/ex001_position_open.json` (position record + signal that triggered it) | [ ] |
| IC-EX-002 | Virtual trader closes position at stop loss | Position closed when price hits stop; P&L matches expected loss | `artifacts/ic_tests/ex002_stop_loss.json` (position record with close reason=STOP, P&L calculation) | [ ] |
| IC-EX-003 | Virtual trader closes position at target | Position closed when price hits target; P&L matches expected gain | `artifacts/ic_tests/ex003_target_hit.json` (position record with close reason=TARGET, P&L calculation) | [ ] |
| IC-EX-004 | Max concurrent positions enforced | Third position attempt rejected when 2 positions already open (paper mode limit) | `artifacts/ic_tests/ex004_max_positions.json` (rejection log with reason=MAX_CONCURRENT) | [ ] |

### 3.3 Risk Controls

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-RC-001 | Circuit breaker L1 (1.5%) triggers position sizing reduction | Daily drawdown reaches 1.5%; next signal has reduced position size | `artifacts/ic_tests/rc001_cb_l1.json` (drawdown event + sizing adjustment log) | [ ] |
| IC-RC-002 | Circuit breaker L2 (2.5%) suspends new entries | Daily drawdown reaches 2.5%; no new positions opened until next session | `artifacts/ic_tests/rc002_cb_l2.json` (suspension event + rejection of subsequent signals) | [ ] |
| IC-RC-003 | Circuit breaker L3 (4.0%) activates kill switch | Daily drawdown reaches 4.0%; kill switch auto-activated; PM/IC notified | `artifacts/ic_tests/rc003_cb_l3.json` (kill switch event + notification log) | [ ] |
| IC-RC-004 | Kill switch halts all signal delivery immediately | Kill switch activated; no signals reach any output channel | `artifacts/ic_tests/rc004_kill_switch.json` (activation event + verification of zero output) | [ ] |
| IC-RC-005 | Kill switch persists across restart | Kill switch activated, engine restarted; kill switch still active after restart | `artifacts/ic_tests/rc005_kill_persist.json` (state file before restart + state after restart) | [ ] |

### 3.4 Data Quality

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-DQ-001 | OHLC integrity: High < Low rejected | Bar with High < Low rejected at data validation layer; never enters scoring | `artifacts/ic_tests/dq001_ohlc_reject.json` (rejection log with bar data) | [ ] |
| IC-DQ-002 | Division-by-zero guards produce safe fallbacks | Zero denominators in return calculations, RVOL, BB width produce 0.0 or 1.0 fallback; no crash | `artifacts/ic_tests/dq002_div_zero.json` (test results for all 11 div/0 locations) | [ ] |
| IC-DQ-003 | Data coverage >= 80% for CORE tickers | At least 80% of ISA universe tickers return valid data during market hours | `artifacts/ic_tests/dq003_data_coverage.json` (per-ticker coverage report) | [ ] |
| IC-DQ-004 | Confidence bounds enforced [0, 100] | Confidence values outside [0, 100] are clamped; NaN confidence blocks signal | `artifacts/ic_tests/dq004_confidence_bounds.json` (boundary test results) | [ ] |

### 3.5 Output Delivery

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-OD-001 | Telegram signal format matches specification | Signal message contains: ticker, direction, score, confidence, regime, entry, stop, target, as-of timestamp | `artifacts/ic_tests/od001_telegram_format.png` (screenshot of Telegram message) | [ ] |
| IC-OD-002 | PDF generates and passes QA gate | All 7 QA checks pass; clean PDF without watermark delivered | `artifacts/ic_tests/od002_pdf_qa_pass.json` (QA log entry) + sample PDF | [ ] |
| IC-OD-003 | DRAFT watermark applied on QA failure | QA failure produces DRAFT-watermarked PDF with failure report on page 1 | `artifacts/ic_tests/od003_draft_pdf.pdf` (sample DRAFT PDF) | [ ] |
| IC-OD-004 | Deduplicated signals not re-sent | Same signal within 5-minute window silently dropped; only one delivery | `artifacts/ic_tests/od004_dedupe.json` (dedupe log showing hash hit) | [ ] |

### 3.6 Monitoring

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-MO-001 | War Room displays all 37 panels without errors | All panels render with data; zero console.error entries | `artifacts/ic_tests/mo001_warroom_clean.json` (Playwright console log) + screenshots | [ ] |
| IC-MO-002 | Go-Live Gate page shows all 8 checks | Gate page at `/gate` renders with 8 check cards, each showing PASS/WARN/FAIL | `artifacts/ic_tests/mo002_gate_page.png` (screenshot) | [ ] |
| IC-MO-003 | System health alerts reach operator within 60s | Data feed failure triggers Telegram alert within 60 seconds | `artifacts/ic_tests/mo003_alert_latency.json` (timestamp comparison: failure event vs alert received) | [ ] |

### 3.7 Rollback

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-RB-001 | Feature flag toggle reverts feature within 60s | Setting `sanity_gate_v2: false` disables magnitude checks on next tick | `artifacts/ic_tests/rb001_flag_toggle.json` (before/after behaviour log) | [ ] |
| IC-RB-002 | LKG restore completes within 5 minutes | Full `restore_lkg.sh` execution restores previous version; health check passes | `artifacts/ic_tests/rb002_lkg_restore.json` (timing log + health check response) | [ ] |
| IC-RB-003 | Emergency kill switch works via all 3 methods | Telegram `/kill ALL`, file touch, API POST all activate kill switch | `artifacts/ic_tests/rb003_kill_methods.json` (activation log for each method) | [ ] |

### 3.8 Security

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-SE-001 | State-mutating API endpoints require authentication | `/api/kill`, `/api/pause`, `/api/resume`, override endpoints return 401 without API key | `artifacts/ic_tests/se001_auth_check.json` (curl responses without key) | [ ] |
| IC-SE-002 | Override audit log is append-only and tamper-evident | Override entries cannot be deleted; sequence number gaps detected | `artifacts/ic_tests/se002_audit_integrity.json` (audit log sample with sequence numbers) | [ ] |
| IC-SE-003 | No sensitive data in output channels | API key, bot token, .env contents never appear in Telegram messages, PDFs, or War Room | `artifacts/ic_tests/se003_data_leak_check.json` (grep results across all output paths) | [ ] |

### 3.9 Performance

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-PF-001 | Scan cycle completes within 45s (P95) | 100 consecutive scan cycles; P95 < 45 seconds | `artifacts/ic_tests/pf001_scan_sla.json` (timing distribution) | [ ] |
| IC-PF-002 | All API endpoints respond within 200ms (P95) | 100 requests per endpoint; P95 < 200ms for all | `artifacts/ic_tests/pf002_api_latency.json` (per-endpoint P95 latency) | [ ] |
| IC-PF-003 | Memory usage stable after 8 hours | RSS growth < 200MB over 8-hour continuous operation | `artifacts/ic_tests/pf003_memory.json` (RSS readings at T=0, T=4h, T=8h) | [ ] |
| IC-PF-004 | System uptime >= 95% during LSE hours | Over 30 sessions, uptime during 08:00-16:30 UK >= 95% | `artifacts/ic_tests/pf004_uptime.json` (per-session uptime log) | [ ] |

### 3.10 Go-Live Gate

| Test ID | Description | Acceptance Criteria | Proof Artifact | Status |
|---------|-------------|-------------------|----------------|--------|
| IC-GL-001 | All 8 Go-Live Gate checks PASS simultaneously | Gate endpoint returns `go_live: true` with 8 PASS checks | `artifacts/ic_tests/gl001_gate_pass.json` (gate response) + `artifacts/ic_tests/gl001_gate_pass.png` (screenshot) | [ ] |
| IC-GL-002 | Gate self-validates: missing check detected | Remove one check function; gate reports FAIL for that check, not silent pass | `artifacts/ic_tests/gl002_self_validation.json` (gate response with missing check FAIL) | [ ] |
| IC-GL-003 | Wiring proof pack complete and valid | All 8 proof pack artifacts present, valid, and less than 7 days old | `artifacts/wiring_proof/` directory listing + pack validation output | [ ] |

---

## 4. MASTER TEST MATRIX

| Test ID | Category | Description | Proof Artifact | Status |
|---------|----------|-------------|----------------|--------|
| IC-SP-001 | Signal Pipeline | E2E qualifying signal delivery | `sp001_signal_e2e.json` | [ ] |
| IC-SP-002 | Signal Pipeline | Score=0 blocked | `sp002_score_zero_block.json` | [ ] |
| IC-SP-003 | Signal Pipeline | Magnitude hold | `sp003_magnitude_hold.json` | [ ] |
| IC-SP-004 | Signal Pipeline | Regime mismatch block | `sp004_regime_mismatch.json` | [ ] |
| IC-SP-005 | Signal Pipeline | Stale data block | `sp005_staleness_block.json` | [ ] |
| IC-EX-001 | Execution Chain | Position open from signal | `ex001_position_open.json` | [ ] |
| IC-EX-002 | Execution Chain | Stop loss execution | `ex002_stop_loss.json` | [ ] |
| IC-EX-003 | Execution Chain | Target hit execution | `ex003_target_hit.json` | [ ] |
| IC-EX-004 | Execution Chain | Max concurrent positions | `ex004_max_positions.json` | [ ] |
| IC-RC-001 | Risk Controls | Circuit breaker L1 | `rc001_cb_l1.json` | [ ] |
| IC-RC-002 | Risk Controls | Circuit breaker L2 | `rc002_cb_l2.json` | [ ] |
| IC-RC-003 | Risk Controls | Circuit breaker L3 | `rc003_cb_l3.json` | [ ] |
| IC-RC-004 | Risk Controls | Kill switch immediate | `rc004_kill_switch.json` | [ ] |
| IC-RC-005 | Risk Controls | Kill switch persistence | `rc005_kill_persist.json` | [ ] |
| IC-DQ-001 | Data Quality | OHLC integrity | `dq001_ohlc_reject.json` | [ ] |
| IC-DQ-002 | Data Quality | Div/zero guards | `dq002_div_zero.json` | [ ] |
| IC-DQ-003 | Data Quality | Data coverage | `dq003_data_coverage.json` | [ ] |
| IC-DQ-004 | Data Quality | Confidence bounds | `dq004_confidence_bounds.json` | [ ] |
| IC-OD-001 | Output Delivery | Telegram format | `od001_telegram_format.png` | [ ] |
| IC-OD-002 | Output Delivery | PDF QA pass | `od002_pdf_qa_pass.json` | [ ] |
| IC-OD-003 | Output Delivery | DRAFT watermark | `od003_draft_pdf.pdf` | [ ] |
| IC-OD-004 | Output Delivery | Dedupe enforcement | `od004_dedupe.json` | [ ] |
| IC-MO-001 | Monitoring | War Room clean render | `mo001_warroom_clean.json` | [ ] |
| IC-MO-002 | Monitoring | Go-Live Gate display | `mo002_gate_page.png` | [ ] |
| IC-MO-003 | Monitoring | Alert latency | `mo003_alert_latency.json` | [ ] |
| IC-RB-001 | Rollback | Feature flag toggle | `rb001_flag_toggle.json` | [ ] |
| IC-RB-002 | Rollback | LKG restore | `rb002_lkg_restore.json` | [ ] |
| IC-RB-003 | Rollback | Kill switch methods | `rb003_kill_methods.json` | [ ] |
| IC-SE-001 | Security | API authentication | `se001_auth_check.json` | [ ] |
| IC-SE-002 | Security | Audit integrity | `se002_audit_integrity.json` | [ ] |
| IC-SE-003 | Security | No data leakage | `se003_data_leak_check.json` | [ ] |
| IC-PF-001 | Performance | Scan SLA | `pf001_scan_sla.json` | [ ] |
| IC-PF-002 | Performance | API latency | `pf002_api_latency.json` | [ ] |
| IC-PF-003 | Performance | Memory stability | `pf003_memory.json` | [ ] |
| IC-PF-004 | Performance | Uptime SLA | `pf004_uptime.json` | [ ] |
| IC-GL-001 | Go-Live Gate | All 8 checks PASS | `gl001_gate_pass.json` | [ ] |
| IC-GL-002 | Go-Live Gate | Self-validation | `gl002_self_validation.json` | [ ] |
| IC-GL-003 | Go-Live Gate | Proof pack complete | `wiring_proof/` listing | [ ] |

---

## 5. SIGN-OFF

### 5.1 Gate 1: Paper Stable (30 sessions)

All 38 tests MUST show [ PASS ] with proof artifacts present and dated within the measurement period.

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Portfolio Manager | _________________ | ____/____/____ | _________________ |
| IC Member | _________________ | ____/____/____ | _________________ |

### 5.2 Gate 2: Paper Ready (60 sessions from Gate 1)

All 38 tests re-verified. Performance tests (IC-PF-001 through IC-PF-004) must reflect the full 60-session measurement period.

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Portfolio Manager | _________________ | ____/____/____ | _________________ |
| IC Member | _________________ | ____/____/____ | _________________ |

### 5.3 Gate 3: Limited Live Approval

All 38 tests verified. Additionally, broker API integration tests must be completed (not covered in this document; defined at Gate 3 entry).

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Portfolio Manager | _________________ | ____/____/____ | _________________ |
| IC Chair | _________________ | ____/____/____ | _________________ |
| Risk Officer | _________________ | ____/____/____ | _________________ |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial IC-level acceptance tests |

---

*End of Document NZT48-ANNEX-ATM-001*
