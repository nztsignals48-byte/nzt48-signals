# NZT-48 Rollback and Feature Flags Matrix

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-RFFM-001           |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- IC/PM Operations   |
| Related         | ROLLBACK_PLAN.md (per-workstream rollback procedures) |

---

## 1. PURPOSE

ROLLBACK_PLAN.md provides per-workstream rollback procedures, LKG restore, and emergency procedures. This document provides the **matrix view**: a single-reference table of all feature flags, their blast radii, dependencies, and revert procedures. This is the document an operator consults at 3am when something breaks.

---

## 2. FEATURE FLAGS REGISTRY

All feature flags reside in `config/settings.yaml` under `feature_flags:`. Flags are read on every scan tick (60s). Changes take effect without restart.

**Default for all flags: `false` (disabled).** This is the safe, pre-Master-Plan behaviour.

| Flag Name | Workstream | Default | Description | Blast Radius | Revert Time |
|-----------|-----------|---------|-------------|-------------|-------------|
| `sanity_gate_v2` | W1 | `false` | Magnitude filter (+-8% overnight, +-30% intraday), data quality gate, Score>=10 check | LOW | < 60s |
| `leverage_once_assertion` | W2 | `false` | Single-leverage return math, div/0 guards, confidence bounds [0,100] | MEDIUM | < 60s |
| `provenance_tracking` | W3 | `false` | TTL matrix validation, provenance records on all data fields | MEDIUM | < 60s |
| `telegram_tape_v2` | W4 | `false` | Category labels, HTML parse_mode, persistent kill/pause state, 5-min quiet mode | LOW | < 60s |
| `persistent_dedupe` | W4 | `false` | Dedupe hashes persisted to SQLite (survives restart) | LOW | < 60s |
| `regime_unification` | W5 | `false` | Canonical regime mapping (5-state to 8-state), single regime source of truth | HIGH | < 60s |
| `drought_escalation` | W5 | `false` | Drought state machine (NORMAL/WATCH/DROUGHT/CRITICAL/CLEARED) | LOW | < 60s |
| `pdf_qa_gate` | W6 | `false` | 7-check pre-flight QA on all PDFs, lane separation enforcement, DRAFT watermarks | LOW | < 60s |
| `war_room_v2` | W7 | `false` | 6 new API endpoints (scan_health, opportunity, exits, telegram/events, consistency, copilot). Disabled = 501 response | LOW | < 60s |
| `datahub_routing` | W8 | `false` | Provider abstraction layer routes data through DataHub instead of direct yfinance | HIGH | < 60s |
| `universe_governance` | W10 | `false` | CORE-first compute budget (70/20/10%), approval gate, low-liquidity handling | MEDIUM | < 60s |
| `learning_loop_hardened` | W11 | `false` | 24h outcome timeout, drift detection, DEFENSIVE mode, readiness gates | MEDIUM | < 60s |
| `always_wired_v1` | W13 | `false` | Master flag for all W13 controls (startup readiness, integrity monitor, self-healing, single source, Docker parity) | LOW | < 60s |

---

## 3. FLAG DEPENDENCY MATRIX

Enabling a downstream flag without its upstream dependency produces degraded or undefined behaviour. When rolling back an upstream flag, also disable all dependent downstream flags.

```
sanity_gate_v2 (W1) ----+
                         |
leverage_once_assertion (W2) ---+
                                |
           provenance_tracking (W3) ---+---+---+
                     |                 |   |   |
        telegram_tape_v2 (W4) ---+     |   |   |
        persistent_dedupe (W4)   |     |   |   |
                                 |     |   |   |
        regime_unification (W5) -+--+  |   |   |
        drought_escalation (W5)  |  |  |   |   |
                                 |  |  |   |   |
                pdf_qa_gate (W6) +  |  |   |   |
                                    |  |   |   |
                 war_room_v2 (W7) --+  |   |   |
                                       |   |   |
              datahub_routing (W8) -----+   |   |
                                            |   |
          universe_governance (W10) --------+   |
                                                |
        learning_loop_hardened (W11) -----------+
```

### Explicit Dependencies

| Downstream Flag | Upstream Dependencies | Effect If Upstream Disabled |
|----------------|----------------------|---------------------------|
| `provenance_tracking` | `leverage_once_assertion` | Provenance may track uncorrected (double-leveraged) return values |
| `telegram_tape_v2` | `provenance_tracking` | Telegram messages cannot attach freshness metadata |
| `pdf_qa_gate` | `provenance_tracking` + `regime_unification` | QA checker cannot verify data freshness or regime consistency |
| `war_room_v2` | `telegram_tape_v2` + `drought_escalation` | War Room missing Telegram desk tape events and drought state panel |
| `datahub_routing` | `provenance_tracking` | Data vendor layer cannot attach provenance records |
| `learning_loop_hardened` | `regime_unification` | Drift detector cannot reference canonical regime states |

---

## 4. BLAST RADIUS CLASSIFICATION

| Level | Definition | Examples | Recovery |
|-------|-----------|----------|----------|
| **NONE** | No impact on signal pipeline or output. Purely informational. | Logging changes, cosmetic UI updates | No recovery needed |
| **LOW** | Affects one output channel or one non-critical subsystem. No impact on signal generation or risk controls. | `pdf_qa_gate` (only affects PDF watermarks), `war_room_v2` (only affects dashboard), `persistent_dedupe` (only affects restart dedup) | Toggle flag off. Normal operations resume on next tick. |
| **MEDIUM** | Affects signal scoring, qualification, or data validation. Signals may be incorrectly scored or filtered. No direct capital risk in paper mode. | `leverage_once_assertion` (return math), `provenance_tracking` (data freshness), `universe_governance` (ticker selection), `learning_loop_hardened` (drift detection) | Toggle flag off. Verify signals resume. Check for any stuck states. |
| **HIGH** | Affects core data routing or regime classification. Widespread impact across multiple subsystems. | `regime_unification` (all regime-dependent decisions), `datahub_routing` (all data feeds) | Toggle flag off. Verify data feeds resume. May need to also toggle dependent flags. Monitor for 5 minutes. |
| **CRITICAL** | Affects capital (live mode only). Direct risk of financial loss. | Not applicable in paper mode. In live mode: broker API issues, position sizing bugs, stop placement errors. | Kill switch first, then diagnose. LKG restore if flag toggle insufficient. |

---

## 5. REVERT PROCEDURES

### 5.1 Per-Flag Revert

For each flag, the revert procedure is identical:

1. Edit `config/settings.yaml` on EC2 host.
2. Set the flag to `false`.
3. Save the file.
4. Wait up to 60 seconds for the next scan tick to pick up the change.
5. Verify the feature is disabled (check logs for flag state output).

```bash
# SSH to EC2 and edit
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11
nano /home/ubuntu/nzt48-signals/config/settings.yaml
# Change the flag to false, save, exit
# Engine reads new config on next tick (max 60s)
```

### 5.2 Multi-Flag Revert (Disable All)

When the root cause is unclear, disable all flags simultaneously:

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 << 'EOF'
cd /home/ubuntu/nzt48-signals
# Set all feature flags to false using sed
sed -i 's/: true$/: false/' config/settings.yaml
echo "All feature flags disabled at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Verify
grep -A 20 'feature_flags:' config/settings.yaml
EOF
```

### 5.3 Dependency-Aware Revert

When disabling an upstream flag, also disable all downstream flags:

| If Disabling | Also Disable |
|-------------|-------------|
| `leverage_once_assertion` | `provenance_tracking`, `datahub_routing`, and all their downstream |
| `provenance_tracking` | `telegram_tape_v2`, `pdf_qa_gate`, `datahub_routing` |
| `regime_unification` | `pdf_qa_gate`, `war_room_v2`, `learning_loop_hardened` |
| `telegram_tape_v2` | `war_room_v2` |
| `drought_escalation` | `war_room_v2` |

---

## 6. LKG RESTORE PROCEDURE

When flag toggle is insufficient (core code is broken, not just a feature):

```
T+0:00  DETECT anomalous behaviour
T+0:30  DECIDE: LKG restore needed
T+1:00  KILL: /kill ALL via Telegram (or touch data/KILL_SWITCH)
T+1:30  STOP: docker compose stop nzt48
T+2:00  RESTORE: ./scripts/restore_lkg.sh [lkg-tag]
T+4:00  VERIFY: curl http://localhost:8000/api/health
T+5:00  RESUME: /resume ALL (if health OK)
```

**Decision criteria: Flag toggle vs LKG restore**

| Symptom | Action |
|---------|--------|
| One feature producing bad output | Disable that feature's flag |
| Multiple features broken | Disable ALL flags |
| Core engine crash loop | LKG restore |
| Artifact corruption | LKG restore + clear `artifacts/` |
| Docker image broken | `docker tag nzt48:<lkg-tag> nzt48:latest` + restart |

---

## 7. EMERGENCY ROLLBACK SEQUENCE

Full system rollback for catastrophic failure. Target: < 5 minutes from detection to restored operations.

| Step | Action | Command | Verification |
|------|--------|---------|-------------|
| 1 | Activate kill switch | `/kill ALL` via Telegram | Telegram confirms "KILL SWITCH ACTIVE" |
| 2 | Stop engine | `docker compose stop nzt48` | `docker ps` shows no nzt48 container |
| 3 | Identify LKG tag | `git tag -l 'lkg-*' --sort=-creatordate \| head -1` | Tag name displayed |
| 4 | Checkout LKG code | `git stash && git checkout <lkg-tag>` | `git log --oneline -1` shows LKG commit |
| 5 | Restore LKG config | `cp data/lkg/settings_<tag>.yaml config/settings.yaml` | Config hash matches LKG |
| 6 | Rebuild container | `docker compose build nzt48` | Build completes without error |
| 7 | Start container | `docker compose up -d nzt48` | `docker ps` shows nzt48 running |
| 8 | Wait for startup | `sleep 15` | -- |
| 9 | Health check | `curl -sf http://localhost:8000/api/health` | HTTP 200 with `status: ok` |
| 10 | Deactivate kill switch | `/resume ALL` via Telegram or `rm data/KILL_SWITCH` | System resumes normal operation |
| 11 | Log incident | Write entry to `data/INCIDENT_LOG.jsonl` | Entry with all required fields |

---

## 8. ACCEPTANCE TESTS

| Test ID | Scenario | Expected Result | Pass Criteria |
|---------|----------|-----------------|---------------|
| RFFM-T01 | Toggle `sanity_gate_v2` from true to false; verify magnitude checks disabled within 60s | Signals with >30% return pass through (no SANITY_FAIL block) | Log confirms flag change; subsequent signal not blocked by magnitude gate |
| RFFM-T02 | Disable upstream flag (`provenance_tracking`); verify downstream flag (`pdf_qa_gate`) also disabled automatically or produces degradation warning | Either QA gate degrades gracefully or operator notified of dependency violation | Log shows dependency warning or QA gate reports "provenance unavailable" |
| RFFM-T03 | Disable ALL flags simultaneously via `sed` command; verify system returns to pre-Master-Plan behaviour | All features revert; signals flow without new gates | `grep -c 'true' config/settings.yaml` under feature_flags returns 0 |
| RFFM-T04 | Execute full LKG restore (`restore_lkg.sh`); verify completes within 5 minutes | System running on LKG code; health check passes | `curl` returns 200; restore duration < 300 seconds |
| RFFM-T05 | Execute emergency rollback sequence (all 11 steps); verify end-to-end | Kill switch -> stop -> LKG restore -> start -> resume completes cleanly | All 11 steps succeed; incident log entry created |
| RFFM-T06 | Verify flag dependency matrix by enabling downstream without upstream; confirm warning | System logs dependency violation warning | Log entry contains "dependency not satisfied" or equivalent |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial matrix specification |

---

*End of Document NZT48-ANNEX-RFFM-001*
