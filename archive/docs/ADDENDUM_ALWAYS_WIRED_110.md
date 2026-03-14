# NZT-48 ADDENDUM: ALWAYS-WIRED INTEGRATION + SELF-HEALING OPS + 110/100 RELIABILITY

**Document ID**: NZT48-ADD-AW-001
**Version**: 1.0
**Date**: 2026-02-27
**Classification**: INTERNAL — PM/IC APPROVAL REQUIRED
**Status**: DRAFT — AWAITING APPROVAL
**Parent**: `docs/IC_RECOVERY_MASTER_PLAN.md` v3.0
**Scope**: Adds Workstream W13 (Always-Wired) to the Recovery Master Plan, plus 9 new annex specifications

---

## 1. WHY THIS ADDENDUM EXISTS

The NZT-48 system keeps failing "first thing" because **components drift out of sync**: engine ↔ data ↔ artifacts ↔ War Room ↔ Telegram ↔ PDFs ↔ learning/AI ↔ EC2/Docker. The Recovery Master Plan v3.0 (W0-W12) addresses individual component defects, but does not address the **integration layer** — the contracts and monitors that keep everything wired together even after changes.

**Observed Integration Failures**:

| # | Failure Pattern | Root Cause | Evidence |
|---|----------------|------------|----------|
| IF-1 | Engine runs but War Room shows stale data | No artifact freshness enforcement between writer and reader | War Room polls API; API serves cached state; no run_id correlation |
| IF-2 | Telegram sends signal while PDF shows different regime | No single-source-of-truth enforcement; PDF may recompute regime independently | `pdf_v2_momentum.py` fetches live data via yfinance during render (lines 33, 346-431) |
| IF-3 | Docker container runs old code after rsync + no rebuild | Code baked at build time; rsync updates host but not container | `Dockerfile` copies code at build; bind-mount only covers `config/` |
| IF-4 | System emits trade signals before data providers are verified on boot | No startup readiness gate; `main.py:3678` runs initial scan immediately | Crash at T=0 if yfinance is unreachable |
| IF-5 | Kill switch lost after restart; duplicate signals sent | In-memory state not persisted (addressed by W4) but no boot gate prevents premature output | `telegram_bot.py:1277,1300` — memory-only state |

**What this addendum adds**: Formal integration contracts, startup readiness gates, continuous wiring monitors, self-healing policies, artifact single-source enforcement, Docker drift guards, and luxury features that keep the system at 110/100 reliability.

---

## 2. NEW CONTROLS INTRODUCED

### W13: Always-Wired Integration + Self-Healing Ops

| Sub-ID | Control | New Annex |
|--------|---------|-----------|
| W13-1 | Integration Contracts — formal interface definitions and invariants | `annexes/INTEGRATION_CONTRACTS.md` (existing, updated) |
| W13-2 | Startup Readiness Gate — pre-flight checklist on boot and session window | `annexes/STARTUP_READINESS_GATE_SPEC.md` (new) |
| W13-3 | Wiring Test Matrix — every must-be-connected path with test method | `annexes/WIRING_TEST_MATRIX.md` (new) |
| W13-4 | Continuous Integrity Monitor — runtime wiring drift detection | `annexes/CONTINUOUS_INTEGRITY_MONITOR_SPEC.md` (new) |
| W13-5 | Self-Healing Ops — auto-remediation policy (safe vs requires human) | `annexes/SELF_HEALING_OPS_SPEC.md` (new) |
| W13-6 | Artifact Single Source Policy — no recomputation drift | `annexes/ARTIFACT_SINGLE_SOURCE_POLICY.md` (new) |
| W13-7 | EC2/Docker Drift Guards — container parity checks | `annexes/EC2_DOCKER_DRIFT_GUARDS.md` (new) |
| W13-8 | War Room System Wiring Panel — manager visibility | `annexes/WAR_ROOM_REQUIREMENTS_SPEC.md` (updated) |
| W13-9 | Luxury Features (110/100) — evidence pack, replay, incident library, etc. | `annexes/LUXURY_FEATURES_110.md` (new) |

---

## 3. THE ALWAYS-WIRED PRINCIPLE

**Core Rule**: Every output channel (Telegram, PDF, War Room) MUST render from the same artifacts for a given run_id. No component may compute, derive, or cache its own version of system-of-record objects.

**System-of-Record Objects** (singular sources of truth — see `annexes/INTEGRATION_CONTRACTS.md`):

| Object | Authoritative Source | Writer | Readers |
|--------|---------------------|--------|---------|
| SystemState | `artifacts/system_state.json` | `command_center/state.py` | War Room, Telegram, PDF, Go-Live Gate, Integrity Monitor |
| RegimeState | `artifacts/system_state.json` (regime field) | Regime mapping module | All consumers via system_state.json only |
| DataHealth | `artifacts/system_state.json` (data_reliability field) | DataFeedValidator | Signal gating, Telegram mode, War Room |
| ScanHealth | `artifacts/scan_health.json` | Main orchestrator tick loop | War Room, Go-Live Gate, Integrity Monitor |
| Plays/Signals | `artifacts/plays.json` | Signal Engine | Telegram, PDF, War Room, Signal Logger |
| DroughtState | `artifacts/drought.json` | Drought Manager | Telegram, PDF, War Room |

**Invariants** (must ALWAYS hold):

1. War Room, Telegram, and PDFs must reference the same `run_id` + `config_hash` + `as_of` timestamps for any given scan cycle
2. No component may compute its own regime or data health independently
3. Telegram and PDF sends are BLOCKED unless health gates PASS
4. "First thing" startup must NOT emit trade outputs until readiness gate returns READY
5. Every artifact write is atomic (write to temp → validate schema → rename to final path)
6. Run manifest produced for every scan cycle as proof of computation provenance

---

## 4. WHAT "PASS" LOOKS LIKE (ACCEPTANCE CRITERIA)

### Startup Readiness Gate
- System boots → 8-check gate runs → returns READY/DEGRADED/HALTED
- If not READY: only [SYSTEM] messages sent; no trade outputs until gate passes
- Gate re-checks every 5 minutes; auto-recovers when checks pass
- Evidence: `artifacts/readiness_gate.json` with timestamped results

### Wiring Integrity (Continuous)
- Every 5 minutes: integrity monitor checks 10 wiring paths
- Any drift detected → DEGRADED mode + [SYSTEM] alert with specific drift signature
- All outputs blocked until drift resolved
- Evidence: `artifacts/integrity_status.json` with check history

### Artifact Single Source
- PDF renderer reads from `artifacts/plays.json` — never fetches live data
- Telegram reads from artifacts — never computes independently
- War Room serves artifacts — never runs its own engine
- Cross-check: run_id in API response matches run_id in artifact file

### Docker Parity
- After every deploy: parity check compares host vs container checksums
- Mismatch → alert + deploy blocked until rebuild
- Daily scheduled parity check via APScheduler

---

## 5. WHAT GETS BLOCKED WHEN SOMETHING BREAKS (FAIL-CLOSED)

| Failure | Detection | Response | Blocked Outputs | Recovery |
|---------|-----------|----------|----------------|----------|
| Startup gate HALTED | Boot-time gate check | [SYSTEM] alert + operator actions | ALL trade outputs | Fix failing check → gate auto-re-checks |
| Wiring drift detected | Integrity monitor (5 min) | DEGRADED mode + [SYSTEM] alert | Signals, briefs, regime, drought messages | Investigate drift signature → fix → monitor auto-recovers |
| Artifact schema invalid | Schema validation on write/read | Output channel blocked | Affected channel only | Re-run engine → artifacts regenerated |
| Docker code mismatch | Parity check post-deploy | Deploy blocked | No outputs affected (pre-deploy) | Rebuild container |
| Run manifest missing | PDF/Telegram pre-send check | Output blocked | Affected output | Re-run engine cycle |

---

## 6. OPERATOR WORKFLOWS

### 6.1 Morning Boot Sequence (Operator)
1. Check Telegram for `[SYSTEM] STARTUP: READY` message at ~06:55 UK
2. If `DEGRADED` or `HALTED`: read reason codes in message → follow operator actions
3. Check War Room → System Wiring panel → all indicators green
4. Check Go-Live Gate page → all 8 checks PASS
5. System is ready for trading session

### 6.2 Integrity Alert Response
1. Receive `[SYSTEM] INTEGRITY ALERT: DRIFT_ENGINE_ARTIFACT`
2. Check War Room → System Wiring panel → identify red indicator
3. Follow playbook action for specific drift signature
4. Monitor auto-recovery (integrity monitor re-checks every 5 min)
5. If not auto-recovered in 15 min → manual intervention per playbook

### 6.3 Manager Daily Review
1. Receive `[DAILY BRIEF]` message at 07:00 UK and 22:00 UK (luxury feature)
2. One-page summary: wiring status, top signals, drought, risks, P&L
3. If anything red → operator has already been alerted; check incident log

---

## 7. RISK / ROLLBACK CONSIDERATIONS

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Startup gate too strict (blocks valid sessions) | MEDIUM | Missed trading window | Configurable gate; manual override with `startup_gate_override: true` in settings.yaml |
| Integrity monitor false positives | LOW | Unnecessary DEGRADED mode | Tunable thresholds; 2-consecutive-fail requirement before alert |
| Single-source policy forces sequential artifact writes | LOW | Slightly slower scan cycle | Artifacts are small JSON files; atomic write is <10ms |
| Docker parity check adds deploy time | LOW | 30s longer deploy | Checksums are fast; only critical files checked |
| Self-healing auto-restart of War Room causes brief outage | LOW | 5s dashboard downtime | Gated: only after 3 consecutive health check failures; logged |

**Rollback**: Feature flag `always_wired_v1` in settings.yaml. Set to `false` to disable all W13 controls (startup gate, integrity monitor, parity checks). System reverts to W0-W12 behavior.

---

## 8. NEW REQUIREMENTS (REQ-057 → REQ-073)

| REQ ID | Requirement | Workstream | Acceptance Test |
|--------|-------------|------------|-----------------|
| REQ-057 | Startup readiness gate — 8-check pre-flight on boot and session window | W13 | T-STARTUP-001 to T-STARTUP-008 |
| REQ-058 | Wiring test matrix — 10 must-be-connected paths verified | W13 | T-WIRE-001 to T-WIRE-010 |
| REQ-059 | Continuous integrity monitor — 5-min wiring drift detection | W13 | T-INTEG-001 to T-INTEG-010 |
| REQ-060 | Self-healing ops — safe auto-actions vs requires human | W13 | T-HEAL-001 to T-HEAL-008 |
| REQ-061 | Artifact single source policy — no recomputation drift | W13 | T-SSP-001 to T-SSP-006 |
| REQ-062 | Run manifest per scan cycle (git_hash, config_hash, universe_hash, providers, as_of) | W13 | T-SSP-003 |
| REQ-063 | EC2/Docker drift guards — container parity check | W13 | T-DRIFT-001 to T-DRIFT-006 |
| REQ-064 | "No host-only production code" policy | W13 | T-DRIFT-003 |
| REQ-065 | War Room System Wiring panel (7 green/amber/red indicators) | W13 | T-PW-021 to T-PW-023 |
| REQ-066 | One-click evidence pack export (ZIP of day's artifacts/logs) | W13 | T-LUX-001 |
| REQ-067 | Deterministic replay mode (re-run historical day from stored artifacts) | W13 | T-LUX-002 |
| REQ-068 | Incident library (every alert becomes incident record) | W13 | T-LUX-003 |
| REQ-069 | Manager one-pager (auto-generated daily brief PDF) | W13 | T-LUX-004 |
| REQ-070 | SLA dashboard (scan tick, outcome resolution, PDF audit, Telegram suppression, provider uptime) | W13 | T-LUX-005 |
| REQ-071 | Change impact simulator (dry-run code/config changes against yesterday's artifacts) | W13 | T-LUX-006 |
| REQ-072 | "First thing" readiness checklist visible in War Room at 06:55 and 13:25 UK | W13 | T-PW-024 |
| REQ-073 | Integration contracts updated with W13 interface definitions | W13 | T-WIRE-001 |

---

## 9. SEQUENCING

W13 depends on:
- **W0** (deployment — system must be running)
- **W3** (provenance — freshness data needed for integrity checks)
- **W5** (regime unification — single regime source needed for drift detection)
- **W12** (feature flags — `always_wired_v1` flag)

W13 should be executed in **Phase 2** alongside W3 and W4, after W0/W1/W2/W5/W12 are complete.

**Internal sequencing within W13**:
1. Integration contracts (W13-1) — defines the rules
2. Artifact single source (W13-6) — enforces the rules
3. Startup readiness gate (W13-2) — boot-time enforcement
4. Continuous integrity monitor (W13-4) — runtime enforcement
5. Self-healing ops (W13-5) — auto-remediation
6. Wiring test matrix (W13-3) — verification
7. Docker drift guards (W13-7) — deployment enforcement
8. War Room panel (W13-8) — visibility
9. Luxury features (W13-9) — extras (P1/P2/P3 phased)

---

## 10. ANNEX INVENTORY (ADDENDUM)

| # | Document | Path | Status |
|---|----------|------|--------|
| 1 | This Addendum | `docs/ADDENDUM_ALWAYS_WIRED_110.md` | DRAFT v1.0 |
| 2 | Integration Contracts | `annexes/INTEGRATION_CONTRACTS.md` | EXISTS (v1.0, updated references) |
| 3 | Wiring Test Matrix | `annexes/WIRING_TEST_MATRIX.md` | NEW |
| 4 | Startup Readiness Gate | `annexes/STARTUP_READINESS_GATE_SPEC.md` | NEW |
| 5 | Continuous Integrity Monitor | `annexes/CONTINUOUS_INTEGRITY_MONITOR_SPEC.md` | NEW |
| 6 | Self-Healing Ops | `annexes/SELF_HEALING_OPS_SPEC.md` | NEW |
| 7 | Artifact Single Source Policy | `annexes/ARTIFACT_SINGLE_SOURCE_POLICY.md` | NEW |
| 8 | EC2/Docker Drift Guards | `annexes/EC2_DOCKER_DRIFT_GUARDS.md` | NEW |
| 9 | Luxury Features 110/100 | `annexes/LUXURY_FEATURES_110.md` | NEW |

**Updated existing documents**:
- `docs/IC_RECOVERY_MASTER_PLAN.md` — W13 workstream added (Appendix G)
- `annexes/TRACEABILITY_MATRIX.csv` — REQ-057 to REQ-073 added
- `annexes/ROLLBACK_PLAN.md` — W13 rollback strategy added
- `annexes/TEST_PLAN.md` — W13 test sections added
- `annexes/WAR_ROOM_REQUIREMENTS_SPEC.md` — System Wiring panel added
- `annexes/OPS_GOVERNANCE_PLAN.md` — Wiring drift change control added

---

**END OF ADDENDUM v1.0**

*This addendum requires PM/IC approval before implementation.*
*All W13 controls are behind feature flag `always_wired_v1`.*
*Paper mode remains active throughout.*
