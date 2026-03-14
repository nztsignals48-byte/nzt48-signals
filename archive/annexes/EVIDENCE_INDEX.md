# Evidence Index

**NZT-48 IC/PM Approval Pack -- Master Evidence Registry**

| Field              | Value                  |
|--------------------|------------------------|
| Document ID        | NZT48-ANNEX-EI-001     |
| Version            | 1.0                    |
| Status             | ACTIVE                 |
| Classification     | INTERNAL -- IC/PM ONLY |
| Author             | NZT-48 Engineering     |
| Date               | 2026-02-27             |
| Review Cycle       | Per session / on-change|

---

## 1. Purpose

This document is the single authoritative registry linking every claim made in the IC/PM Approval Pack (the "Binder") to its supporting evidence artifact. No claim in the Binder may exist without a corresponding entry in this index. No evidence artifact may be cited without appearing here.

**Governing Principle:** If it is not in this index, it is not evidence.

---

## 2. Conventions

- **evidence_id**: Unique identifier in the format `EV-NNN`. Ranges are allocated by category (see below).
- **path**: Relative to repository root (`/Users/rr/nzt48-signals/`). Directories end with `/`.
- **date**: ISO-8601 date the artifact was created or last materially updated.
- **used_in_sections**: Binder section references (e.g., "Binder SS3") or Annex identifiers.

### Evidence ID Ranges

| Range       | Category                        |
|-------------|----------------------------------|
| EV-001--019 | Runtime Artifacts (scan outputs) |
| EV-020--029 | PDF Reports                      |
| EV-030--039 | Documentation & Plans            |
| EV-040--049 | Audits & QA                      |
| EV-050--059 | Specifications                   |
| EV-060--069 | Configuration                    |
| EV-070--089 | IC/PM Pack Deliverables          |
| EV-100--199 | Reserved: Trade Records          |
| EV-200--299 | Reserved: Backtest Results       |
| EV-300--399 | Reserved: Broker Integration     |

---

## 3. Evidence Registry

### 3.1 Runtime Artifacts (EV-001 -- EV-012)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-001 | System plays (3 signals: 2 APPROVE, 1 VETO) | `artifacts/2026-02-27/preview_copilot_scan/plays.json` | 2026-02-27 | Binder SS3, SS4 |
| EV-002 | Strategy state (6 active, 12 inactive) | `artifacts/2026-02-27/preview_copilot_scan/strategies.json` | 2026-02-27 | Binder SS2, SS3 |
| EV-003 | Market intelligence (14 intel cards) | `artifacts/2026-02-27/preview_copilot_scan/intel.json` | 2026-02-27 | Binder SS2, SS3 |
| EV-004 | Drought state (NORMAL, no drought) | `artifacts/2026-02-27/preview_copilot_scan/drought.json` | 2026-02-27 | Binder SS3 |
| EV-005 | Risk officer decisions (2 approve, 1 veto) | `artifacts/2026-02-27/preview_copilot_scan/risk_officer.json` | 2026-02-27 | Binder SS3, SS6 |
| EV-006 | Root drought state (NORMAL) | `artifacts/drought.json` | 2026-02-27 | Binder SS3 |
| EV-007 | Pre-LSE scan artifacts (9 files) | `artifacts/2026-02-26/preview_pre_lse/` | 2026-02-26 | Binder SS3 |
| EV-008 | Pre-NYSE scan artifacts | `artifacts/2026-02-26/preview_pre_nyse/` | 2026-02-26 | Binder SS3 |
| EV-009 | EOD institutional scan artifacts | `artifacts/2026-02-26/preview_eod_institutional/` | 2026-02-26 | Binder SS3 |
| EV-010 | Universe core tickers | `artifacts/2026-02-26/universe/core.json` | 2026-02-26 | Binder SS2 |
| EV-011 | Universe expansion v2 verification | `artifacts/universe/expansion_v2_verification.json` | 2026-02-27 | Annex L |
| EV-012 | Universe expansion v3 verification | `artifacts/universe/expansion_v3_verification.json` | 2026-02-27 | Annex L |

### 3.2 PDF Reports (EV-020 -- EV-024)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-020 | Momentum Preview Pre-LSE | `reports/2026-02-27/NZT48_MOMENTUM_PREVIEW_PRE_LSE.pdf` | 2026-02-27 | Binder SS9 |
| EV-021 | Risk Preview Pre-NYSE | `reports/2026-02-27/NZT48_RISK_PREVIEW_PRE_NYSE.pdf` | 2026-02-27 | Binder SS9 |
| EV-022 | EOD Review | `reports/2026-02-27/NZT48_REVIEW_PREVIEW_EOD_INSTITUTIONAL.pdf` | 2026-02-27 | Binder SS9 |
| EV-023 | Pre-LSE Momentum (Feb 26) | `reports/2026-02-26/NZT48_MOMENTUM_PREVIEW_PRE_LSE.pdf` | 2026-02-26 | Binder SS9 |
| EV-024 | Delivery batch proof | `reports/DELIVERY_BATCH_PROOF.md` | 2026-02-27 | Binder SS9 |

### 3.3 Documentation & Plans (EV-030 -- EV-038)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-030 | IC Recovery Master Plan (13 workstreams, 73 REQs) | `docs/IC_RECOVERY_MASTER_PLAN.md` | 2026-02-27 | Binder SS5 |
| EV-031 | Addendum W13 Always-Wired | `docs/ADDENDUM_ALWAYS_WIRED_110.md` | 2026-02-27 | Binder SS5 |
| EV-032 | Executive Summary for PM/IC | `reports/EXEC_SUMMARY_FOR_PM_IC.md` | 2026-02-27 | Binder SS1 |
| EV-033 | Institutional Fix Plan (10 workstreams) | `INSTITUTIONAL_FIX_PLAN.md` | 2026-02-27 | Binder SS5 |
| EV-034 | Signal Pipeline Checklist | `docs/SIGNAL_PIPELINE_CHECKLIST.md` | 2026-02-27 | Binder SS5 |
| EV-035 | Signal Truth Table | `docs/SIGNAL_TRUTH_TABLE.md` | 2026-02-27 | Binder SS5 |
| EV-036 | Paper Launch Audit | `docs/PAPER_LAUNCH_AUDIT.md` | 2026-02-27 | Binder SS15 |
| EV-037 | Go-Live Checklist | `docs/GO_LIVE_CHECKLIST.md` | 2026-02-27 | Binder SS15 |
| EV-038 | Data Vendor Migration Plan | `docs/DATA_VENDOR_MIGRATION_PLAN.md` | 2026-02-27 | Binder SS7 |

### 3.4 Audits & QA (EV-040 -- EV-046)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-040 | Codebase Line-by-Line Audit | `reports/audit_2026-02-27/CODEBASE_AUDIT_LINE_BY_LINE.md` | 2026-02-27 | Binder SS3, SS4 |
| EV-041 | PDF QA Audit | `reports/audit_2026-02-27/PDF_QA_AUDIT_LATEST.md` | 2026-02-27 | Binder SS9 |
| EV-042 | War Room QA Report | `reports/audit_2026-02-27/WAR_ROOM_QA_REPORT.md` | 2026-02-27 | Binder SS10 |
| EV-043 | Operational Audit | `reports/audit_2026-02-27/AUDIT_TODAY_OPERATIONAL.md` | 2026-02-27 | Binder SS3 |
| EV-044 | Scope Alignment Audit | `annexes/SCOPE_ALIGNMENT_AUDIT.md` | 2026-02-27 | Binder SS5 |
| EV-045 | Forensics Map | `annexes/FORENSICS_MAP.md` | 2026-02-27 | Binder SS3, SS4 |
| EV-046 | Traceability Matrix (56 REQs, 181 tests) | `annexes/TRACEABILITY_MATRIX.csv` | 2026-02-27 | Binder SS11 |

### 3.5 Specifications (EV-050 -- EV-057)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-050 | Integration Contracts | `annexes/INTEGRATION_CONTRACTS.md` | 2026-02-27 | Binder SS2 |
| EV-051 | Test Plan (240 tests) | `annexes/TEST_PLAN.md` | 2026-02-27 | Binder SS11 |
| EV-052 | Wiring Test Matrix | `annexes/WIRING_TEST_MATRIX.md` | 2026-02-27 | Binder SS11 |
| EV-053 | Output Policy (8 gates) | `annexes/OUTPUT_POLICY_SPEC.md` | 2026-02-27 | Binder SS9 |
| EV-054 | Provenance Spec | `annexes/PROVENANCE_SPEC.md` | 2026-02-27 | Binder SS7 |
| EV-055 | Regime/Drought Spec | `annexes/REGIME_DROUGHT_SPEC.md` | 2026-02-27 | Binder SS2 |
| EV-056 | Rollback Plan | `annexes/ROLLBACK_PLAN.md` | 2026-02-27 | Binder SS5, SS13 |
| EV-057 | Startup Readiness Gate | `annexes/STARTUP_READINESS_GATE_SPEC.md` | 2026-02-27 | Binder SS2 |

### 3.6 Configuration (EV-060 -- EV-061)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-060 | Master config (993 lines) | `config/settings.yaml` | 2026-02-27 | Binder SS2 |
| EV-061 | Manual Actions Required | `reports/MANUAL_ACTIONS_REQUIRED.md` | 2026-02-27 | Binder SS5 |

### 3.7 IC/PM Pack Deliverables (EV-070 -- EV-079)

| evidence_id | description | path | date | used_in_sections |
|---|---|---|---|---|
| EV-070 | Gap Analysis | `reports/GAP_ANALYSIS_WHATS_MISSING.md` | 2026-02-27 | Binder SS1 |
| EV-071 | Decision Register | `annexes/DECISION_REGISTER.md` | 2026-02-27 | Binder SS16 |
| EV-072 | Risk Constitution | `annexes/RISK_CONSTITUTION.md` | 2026-02-27 | Binder SS6 |
| EV-073 | Change Control Policy | `annexes/CHANGE_CONTROL_POLICY.md` | 2026-02-27 | Binder SS13 |
| EV-074 | Evidence & Reproducibility Spec | `annexes/EVIDENCE_AND_REPRODUCIBILITY_SPEC.md` | 2026-02-27 | Binder SS7 |
| EV-075 | Model Risk MRM Spec | `annexes/MODEL_RISK_MRM_SPEC.md` | 2026-02-27 | Binder SS6 |
| EV-076 | Execution Realism Spec | `annexes/EXECUTION_REALISM_SPEC.md` | 2026-02-27 | Binder SS8 |
| EV-077 | Observability & Monitoring Spec | `annexes/OBSERVABILITY_MONITORING_SPEC.md` | 2026-02-27 | Binder SS12 |
| EV-078 | Incident Response Playbook | `annexes/INCIDENT_RESPONSE_PLAYBOOK.md` | 2026-02-27 | Binder SS12 |
| EV-079 | Security & Secrets Spec | `annexes/SECURITY_AND_SECRETS_SPEC.md` | 2026-02-27 | Binder SS14 |

---

## 4. Evidence Needed

The following evidence categories are required for full IC/PM approval but are **not yet available**. Each entry identifies what is missing, why it matters, and the expected delivery timeline.

| evidence_id | description | status | blocker | expected_date | used_in_sections |
|---|---|---|---|---|---|
| EV-100 | Paper trade log (minimum 60 trading days) | NOT AVAILABLE | System in paper mode; no trade history yet | T+60 trading days from go-live | Binder SS4, SS8 |
| EV-101 | Paper trade PnL attribution report | NOT AVAILABLE | Depends on EV-100 | T+60 trading days from go-live | Binder SS4, SS8 |
| EV-102 | Slippage analysis (paper vs. hypothetical fills) | NOT AVAILABLE | Depends on EV-100 | T+60 trading days from go-live | Binder SS8 |
| EV-103 | Strategy hit-rate breakdown per strategy (S1-S15) | NOT AVAILABLE | Depends on EV-100 | T+60 trading days from go-live | Binder SS4 |
| EV-200 | Full backtest report (S15 Daily Target, 2-year lookback) | NOT AVAILABLE | Backtest engine not yet run against full LSE leveraged universe | TBD | Binder SS4, SS8 |
| EV-201 | Walk-forward validation results | NOT AVAILABLE | Depends on EV-200 | TBD | Binder SS4 |
| EV-202 | Monte Carlo simulation (drawdown distribution) | NOT AVAILABLE | Depends on EV-200 | TBD | Binder SS6 |
| EV-203 | Regime-conditioned backtest (bull/bear/sideways splits) | NOT AVAILABLE | Depends on EV-200 | TBD | Binder SS4 |
| EV-300 | Live broker connectivity test (Interactive Brokers) | NOT AVAILABLE | Broker account not yet configured | Pre-live gate | Binder SS8, SS15 |
| EV-301 | Order routing latency benchmark | NOT AVAILABLE | Depends on EV-300 | Pre-live gate | Binder SS8 |
| EV-302 | FIX/API message log (broker handshake proof) | NOT AVAILABLE | Depends on EV-300 | Pre-live gate | Binder SS8 |
| EV-303 | Margin and position limit verification | NOT AVAILABLE | Depends on EV-300 | Pre-live gate | Binder SS6, SS8 |

**Note to IC/PM:** No claim in the Binder asserts the existence of trade records, backtest results, or broker integration evidence. All claims are scoped to system design, code quality, risk framework, and operational readiness. The above items represent the evidence gap between current state and full production approval.

---

## 5. Evidence Freshness Policy

Each evidence type has a defined refresh cadence. Stale evidence must be flagged in the Binder and may not be cited as current.

| Category | Evidence IDs | Refresh Cadence | Staleness Threshold | Refresh Trigger |
|---|---|---|---|---|
| Runtime Artifacts (scan outputs) | EV-001 -- EV-009 | Every scan cycle (3x daily) | >24 hours | Automatic (scheduler) |
| Universe definitions | EV-010 -- EV-012 | On universe change | >7 days without review | Manual (universe governance) |
| PDF Reports | EV-020 -- EV-023 | Every scan cycle (3x daily) | >24 hours | Automatic (scheduler) |
| Delivery Batch Proof | EV-024 | On each delivery batch | >24 hours | Automatic (delivery pipeline) |
| Recovery/Fix Plans | EV-030, EV-031, EV-033 | On material code change | >14 days without review | Manual (change control) |
| Executive Summary | EV-032 | On Binder update | >7 days | Manual (IC prep) |
| Checklists & Truth Tables | EV-034 -- EV-037 | On pipeline change | >14 days without review | Manual (change control) |
| Data Vendor Migration | EV-038 | On vendor change | >30 days without review | Manual |
| Code Audits | EV-040 -- EV-043 | On material code change | >7 days after code change | Manual (audit team) |
| Scope/Forensics Audits | EV-044 -- EV-045 | On scope change | >14 days | Manual |
| Traceability Matrix | EV-046 | On REQ or test change | >7 days after change | Manual (QA) |
| Specifications | EV-050 -- EV-057 | On spec change | >30 days without review | Manual (change control) |
| Master Config | EV-060 | On config change | >7 days after change | Manual |
| Manual Actions | EV-061 | On action completion/addition | >7 days | Manual |
| IC/PM Pack Deliverables | EV-070 -- EV-079 | On Binder update | >14 days without review | Manual (IC prep) |
| Trade Records (future) | EV-100 -- EV-103 | Daily (post-go-live) | >1 trading day | Automatic (post-go-live) |
| Backtest Results (future) | EV-200 -- EV-203 | On model/strategy change | >90 days without re-run | Manual |
| Broker Integration (future) | EV-300 -- EV-303 | On broker config change | >30 days | Manual |

---

## 6. Verification Procedure

### 6.1 Artifact Existence Check

For every evidence entry in this index, verify the artifact exists at the stated path:

```bash
# Run from repository root: /Users/rr/nzt48-signals/
# Verify all indexed artifacts exist

MISSING=0
while IFS='|' read -r eid path; do
  path=$(echo "$path" | xargs)  # trim whitespace
  if [ ! -e "$path" ]; then
    echo "MISSING: $eid -> $path"
    MISSING=$((MISSING + 1))
  fi
done <<'MANIFEST'
EV-001 | artifacts/2026-02-27/preview_copilot_scan/plays.json
EV-002 | artifacts/2026-02-27/preview_copilot_scan/strategies.json
EV-003 | artifacts/2026-02-27/preview_copilot_scan/intel.json
EV-004 | artifacts/2026-02-27/preview_copilot_scan/drought.json
EV-005 | artifacts/2026-02-27/preview_copilot_scan/risk_officer.json
EV-006 | artifacts/drought.json
EV-007 | artifacts/2026-02-26/preview_pre_lse/
EV-008 | artifacts/2026-02-26/preview_pre_nyse/
EV-009 | artifacts/2026-02-26/preview_eod_institutional/
EV-010 | artifacts/2026-02-26/universe/core.json
EV-011 | artifacts/universe/expansion_v2_verification.json
EV-012 | artifacts/universe/expansion_v3_verification.json
EV-020 | reports/2026-02-27/NZT48_MOMENTUM_PREVIEW_PRE_LSE.pdf
EV-021 | reports/2026-02-27/NZT48_RISK_PREVIEW_PRE_NYSE.pdf
EV-022 | reports/2026-02-27/NZT48_REVIEW_PREVIEW_EOD_INSTITUTIONAL.pdf
EV-023 | reports/2026-02-26/NZT48_MOMENTUM_PREVIEW_PRE_LSE.pdf
EV-024 | reports/DELIVERY_BATCH_PROOF.md
EV-030 | docs/IC_RECOVERY_MASTER_PLAN.md
EV-031 | docs/ADDENDUM_ALWAYS_WIRED_110.md
EV-032 | reports/EXEC_SUMMARY_FOR_PM_IC.md
EV-033 | INSTITUTIONAL_FIX_PLAN.md
EV-034 | docs/SIGNAL_PIPELINE_CHECKLIST.md
EV-035 | docs/SIGNAL_TRUTH_TABLE.md
EV-036 | docs/PAPER_LAUNCH_AUDIT.md
EV-037 | docs/GO_LIVE_CHECKLIST.md
EV-038 | docs/DATA_VENDOR_MIGRATION_PLAN.md
EV-040 | reports/audit_2026-02-27/CODEBASE_AUDIT_LINE_BY_LINE.md
EV-041 | reports/audit_2026-02-27/PDF_QA_AUDIT_LATEST.md
EV-042 | reports/audit_2026-02-27/WAR_ROOM_QA_REPORT.md
EV-043 | reports/audit_2026-02-27/AUDIT_TODAY_OPERATIONAL.md
EV-044 | annexes/SCOPE_ALIGNMENT_AUDIT.md
EV-045 | annexes/FORENSICS_MAP.md
EV-046 | annexes/TRACEABILITY_MATRIX.csv
EV-050 | annexes/INTEGRATION_CONTRACTS.md
EV-051 | annexes/TEST_PLAN.md
EV-052 | annexes/WIRING_TEST_MATRIX.md
EV-053 | annexes/OUTPUT_POLICY_SPEC.md
EV-054 | annexes/PROVENANCE_SPEC.md
EV-055 | annexes/REGIME_DROUGHT_SPEC.md
EV-056 | annexes/ROLLBACK_PLAN.md
EV-057 | annexes/STARTUP_READINESS_GATE_SPEC.md
EV-060 | config/settings.yaml
EV-061 | reports/MANUAL_ACTIONS_REQUIRED.md
EV-070 | reports/GAP_ANALYSIS_WHATS_MISSING.md
EV-071 | annexes/DECISION_REGISTER.md
EV-072 | annexes/RISK_CONSTITUTION.md
EV-073 | annexes/CHANGE_CONTROL_POLICY.md
EV-074 | annexes/EVIDENCE_AND_REPRODUCIBILITY_SPEC.md
EV-075 | annexes/MODEL_RISK_MRM_SPEC.md
EV-076 | annexes/EXECUTION_REALISM_SPEC.md
EV-077 | annexes/OBSERVABILITY_MONITORING_SPEC.md
EV-078 | annexes/INCIDENT_RESPONSE_PLAYBOOK.md
EV-079 | annexes/SECURITY_AND_SECRETS_SPEC.md
MANIFEST

if [ "$MISSING" -eq 0 ]; then
  echo "ALL EVIDENCE ARTIFACTS VERIFIED PRESENT"
else
  echo "WARNING: $MISSING artifact(s) missing"
fi
```

### 6.2 Content Integrity Check

For JSON artifacts, verify they parse without error and contain expected top-level keys:

```bash
# Verify JSON artifact integrity
for f in \
  artifacts/2026-02-27/preview_copilot_scan/plays.json \
  artifacts/2026-02-27/preview_copilot_scan/strategies.json \
  artifacts/2026-02-27/preview_copilot_scan/intel.json \
  artifacts/2026-02-27/preview_copilot_scan/drought.json \
  artifacts/2026-02-27/preview_copilot_scan/risk_officer.json \
  artifacts/drought.json \
  artifacts/2026-02-26/universe/core.json \
  artifacts/universe/expansion_v2_verification.json \
  artifacts/universe/expansion_v3_verification.json; do
  if python3 -c "import json; json.load(open('$f'))" 2>/dev/null; then
    echo "OK: $f"
  else
    echo "FAIL: $f (invalid JSON or missing)"
  fi
done
```

### 6.3 PDF Integrity Check

For PDF reports, verify they are valid PDF files and are non-empty:

```bash
for f in \
  reports/2026-02-27/NZT48_MOMENTUM_PREVIEW_PRE_LSE.pdf \
  reports/2026-02-27/NZT48_RISK_PREVIEW_PRE_NYSE.pdf \
  reports/2026-02-27/NZT48_REVIEW_PREVIEW_EOD_INSTITUTIONAL.pdf \
  reports/2026-02-26/NZT48_MOMENTUM_PREVIEW_PRE_LSE.pdf; do
  if [ -f "$f" ] && [ -s "$f" ] && head -c 5 "$f" | grep -q '%PDF'; then
    echo "OK: $f (valid PDF)"
  else
    echo "FAIL: $f (missing, empty, or not a valid PDF)"
  fi
done
```

### 6.4 Cross-Reference Check

Verify that every Binder section cites at least one evidence artifact from this index:

| Binder Section | Required Evidence (minimum) | Status |
|---|---|---|
| SS1 -- Executive Summary | EV-032, EV-070 | Covered |
| SS2 -- System Architecture | EV-002, EV-003, EV-010, EV-050, EV-055, EV-057, EV-060 | Covered |
| SS3 -- Signal Pipeline | EV-001 -- EV-009, EV-040, EV-043, EV-045 | Covered |
| SS4 -- Strategy Analysis | EV-001, EV-002, EV-040, EV-045 | Covered |
| SS5 -- Recovery & Remediation | EV-030, EV-031, EV-033, EV-034, EV-035, EV-044, EV-056, EV-061 | Covered |
| SS6 -- Risk Framework | EV-005, EV-072, EV-075 | Covered |
| SS7 -- Data Provenance | EV-038, EV-054, EV-074 | Covered |
| SS8 -- Execution Realism | EV-076 | Covered (partial; EV-100 -- EV-103, EV-300 -- EV-303 needed for full) |
| SS9 -- PDF Output Quality | EV-020 -- EV-024, EV-041, EV-053 | Covered |
| SS10 -- War Room | EV-042 | Covered |
| SS11 -- Testing | EV-046, EV-051, EV-052 | Covered |
| SS12 -- Observability | EV-077, EV-078 | Covered |
| SS13 -- Change Control | EV-056, EV-073 | Covered |
| SS14 -- Security | EV-079 | Covered |
| SS15 -- Go-Live Readiness | EV-036, EV-037 | Covered |
| SS16 -- Decision Register | EV-071 | Covered |

### 6.5 Verification Sign-Off

Each verification run must be recorded with:

1. **Date and time** of verification
2. **Verifier identity** (person or automated system)
3. **Result** (PASS / FAIL with details)
4. **Evidence gaps** identified (if any)
5. **Remediation actions** taken (if any)

Verification must be performed:
- Before every IC/PM submission
- After any material code deployment
- After any Binder update
- Weekly during paper trading phase

---

## 7. Document Control

| Version | Date | Author | Change Description |
|---|---|---|---|
| 1.0 | 2026-02-27 | NZT-48 Engineering | Initial evidence index created |

---

*End of Document -- NZT48-ANNEX-EI-001*
