# PLAN 2: IMPLEMENTATION-NATIVE BUILD ROADMAP

**Version:** 1.0 — 2026-03-22
**Canonical source:** PLAN_2_MERGED_FINAL.md (3,871 lines, build-ready)
**Purpose:** Convert canonical plan into executable chunk-by-chunk build sprints
**Doctrine:** Rust owns execution. Claude owns intelligence. Ouroboros owns learning. Operator owns authority.

---

## PART 1: IMPLEMENTATION STRATEGY

### Implementation Doctrine
1. **Build in dependency order.** Never build a consumer before its data source exists.
2. **Test before handoff.** Every sprint has explicit pass/fail criteria. No "it probably works."
3. **Rollback before rollout.** Every sprint defines how to undo itself.
4. **Shadow before live.** Claude roles start as reporting-only for 50+ trades.
5. **Artifacts before done.** Sprint is not complete until handoff artifacts exist.
6. **One owner per sprint.** No shared responsibility.
7. **Deterministic first.** Hot-path changes require Rust unit tests. Cold-path changes require JSON schema validation.
8. **Continuous march.** Sprints flow into each other. No idle gaps.

### Sequencing Doctrine
- Foundational correctness → Data governance → Path stabilization → Universe pipeline → Risk hardening → Telemetry → Claude intelligence → Ouroboros governance → Shadow validation → Luxury layers
- Each layer unlocks the next. Skipping layers creates hidden debt.

### Dependency Doctrine
- Explicit `DEPENDS_ON: [Sprint IDs]` for every sprint
- Hard dependency = cannot start until predecessor passes
- Soft dependency = can start in parallel but merge requires predecessor complete

### Testing Doctrine
- Every sprint has: unit tests, integration tests, smoke tests
- Mandatory tests must pass for sprint completion
- Advisory tests inform quality but don't block
- Replay tests required for any hot-path change

### Rollback Doctrine
- Every sprint defines: trigger, method, post-rollback verification
- Rollback must be executable in <5 minutes
- Config rollbacks: restore previous TOML/JSON from git
- Code rollbacks: git revert + docker compose build + docker compose up -d
- Claude rollbacks: disable cron entry (zero impact on trading)

### Artifact Doctrine
- Every sprint produces: updated file list, test results, operator notes
- Handoff template standardized (see Part 7)
- Artifacts stored in `/tasks/sprint_XX/` directory

### Shadow-First Doctrine
- All Claude roles start as REPORTING ONLY
- Promotion requires: 50+ trades, valid JSON 100%, operator approval
- Auto-rollback: WR drop >10% over 50 trades → revert to deterministic

### Promotion Doctrine
- Grade A (50+ trades, p<0.01): auto-promote within bounds
- Grade B (30-49 trades, p<0.05): operator awareness required
- Grade C (10-29 trades): shadow-only
- Grade D (<10 trades): logged and deferred

---

## PART 2: SPRINT MASTER INDEX

| Sprint | Name | Objective | Classification | Path | Owner | Claude Role | Depends On | Success Condition |
|--------|------|-----------|---------------|------|-------|-------------|-----------|-------------------|
| S01 | Foundational Verification | Verify all Plan 1 controls are solid | BUILD NOW | Hot/Warm | Operator | Forbidden | None | All 10 foundational controls pass |
| S02 | Data Governance Wiring | Enforce P0/P1/P2 classification in code | BUILD NOW | All | Operator | Forbidden | S01 | Ownership map verified, no shared-write on P0 |
| S03 | Claude CLI Install | Install Node.js + Claude Code CLI on EC2 | BUILD NOW | Cold | Operator | Subject | S01 | `claude -p` returns valid JSON |
| S04 | Claude Helper Module | Create claude_helper.py shared utilities | BUILD NOW | Cold | Operator | Subject | S03 | Module importable, claude_query() works |
| S05 | Nightly Pipeline Script (H1) | Replace individual cron entries with pipeline.sh | BUILD NOW | Warm | Operator | Forbidden | S01 | Sequential execution, flock prevents parallel |
| S06 | TOML Validation (H3) | Add tomllib parse before every TOML write | BUILD NOW | Warm | Operator | Forbidden | S01 | Zero corrupt TOML writes in 50 trades |
| S07 | Forensic Review API→CLI | Switch claude_review.py from API SDK to CLI | BUILD NOW | Cold | Primary (Role A) | S03, S04 | Valid JSON review 5 consecutive nights |
| S08 | Ouroboros Challenger | Create ouroboros_challenger.py | BUILD NOW | Cold | Primary (Role B) | S07 | Catches ≥1 weak rec per 50 trades |
| S09 | Approval Gate | Create approval_gate.py with hard bounds | BUILD NOW | Cold | Primary (Role C) | S08, S06 | Routes 100% correctly, audit trail complete |
| S10 | Drift Cap (H5) | Add 30-day baseline tracking to approval gate | BUILD NOW | Cold | Governed | S09 | Drift >50% blocked and alerted |
| S11 | Morning Briefing | Switch claude_briefing.py API→CLI, add context | BUILD NOW | Cold | Primary (Role D) | S03, S04 | Telegram delivered by 07:45 UTC 100% |
| S12 | Evening Briefing | Add --evening mode to claude_briefing.py | BUILD NOW | Cold | Primary (Role E) | S11 | Telegram delivered by 21:30 UTC 100% |
| S13 | Curation Shadow | Create claude_curation.py shadow mode | SHADOW FIRST | Cold | Shadow (Role F) | S09 | 100 trade comparison logged |
| S14 | Gate Calibration | Create claude_rejected_review.py | BUILD NOW | Cold | Primary (Role G) | S07 | Weekly report identifies ≥1 adjustment |
| S15 | Anomaly Assessor | Create claude_anomaly.py | BUILD NOW | Cold | Primary (Role H) | S03, S04 | Assessment <30s of trigger |
| S16 | Macro Interpreter | Create claude_macro.py | BUILD NOW | Cold | Primary (Role I) | S03, S04 | Pre-event analysis 30min before |
| S17 | SDE Sandbox | Create Dockerfile.sde-sandbox + first scenario | BUILD NOW | Cold | Research only | S03 | Engine survives flash crash without panic |
| S18 | Operator Psych Audit (H6) | Weekly intervention analysis | BUILD NOW | Cold | Primary | S07 | Sunday report generated |
| S19 | SEC/RNS Scanner (H7) | Filing semantic delta | SHADOW FIRST | Cold | Primary | S03, S04 | Filing diff generated for top 20 tickers |
| S20 | Curation Promotion Gate | Validate shadow → promote or kill | VERIFY LATER | Cold | Operator | S13 + 100 trades | Evidence Grade A or kill |
| S21 | Alpha Model Shadow | Shadow unified alpha alongside current | SHADOW FIRST | Warm | Forbidden | 200+ trades | Shadow outperforms or kill |
| S22 | 2-Factor Kelly Shadow | Shadow simplified Kelly alongside 12-factor | SHADOW FIRST | Warm | Forbidden | 200+ trades | Equivalent or better sizing |
| S23 | Gemini Audit Fixes (Batch 1) | Implement accepted Gemini deep-tier fixes | BUILD NOW | Hot/Warm | Operator | S01 | All accepted fixes verified |
| S24 | Gemini Audit Fixes (Batch 2) | Implement deferred Gemini fixes | CALIBRATE LATER | Various | Operator | S23 + evidence | Each fix proven necessary |

---

## PART 3: DETAILED SPRINT SPECS

### SPRINT S01: FOUNDATIONAL VERIFICATION

**A. Purpose:** Verify all Plan 1 (Sprints 0-10) controls are solid before adding intelligence layer.

**B. Scope In:**
- WAL replay parity check
- Config parse fail-closed verification
- SIGHUP crash-safety verification
- Chandelier ratchet-only verification
- ISA short rejection verification
- Atomic TOML write verification
- Docker healthcheck verification
- Broker reconnect verification
- Drawdown circuit breaker verification
- Equity floor verification

**C. Scope Out:** No new code. Verification only.

**D. Dependencies:** None (first sprint)

**E. Inputs Required:**
- Running EC2 instance with aegis-v2 container
- IBKR Gateway connected
- At least 1 WAL file with trade data

**F. Systems Touched:** None (read-only verification)

**G. Exact Build Tasks:**
1. SSH to EC2, restart aegis-v2, verify WAL replay restores positions/equity/regime
2. Corrupt config.toml temporarily, verify engine refuses to start
3. Send SIGHUP with valid dynamic_weights.toml, verify no crash
4. Send SIGHUP with malformed TOML, verify engine continues with previous config
5. Verify CHECK 1 rejects short direction in logs
6. Verify `cargo check --release` and `cargo check --release --tests` clean locally
7. Verify Docker healthcheck passes (`docker compose ps` shows healthy)
8. Kill IB Gateway, verify engine enters HALT, restart gateway, verify recovery
9. Review WAL events for correct schema version (V10)
10. Verify config hash (V9) logged at startup

**H. Exact Deliverables:**
- `tasks/sprint_01/verification_report.md` with pass/fail for each control
- Screenshot of docker compose ps showing all containers healthy

**I. Exact Tests:**
- [MANDATORY] WAL replay restores equity within £0.01
- [MANDATORY] Malformed config → engine refuses to start
- [MANDATORY] Malformed SIGHUP → engine continues
- [MANDATORY] Chandelier stop never decreases (check WAL RungAdvanced events)
- [MANDATORY] cargo check --release clean
- [ADVISORY] Broker reconnect within 60s

**J. Validation Evidence:** verification_report.md with timestamps

**K. Shadow/Live:** N/A (verification only, no code changes)

**L. Rollback:** N/A

**M. Failure Modes:** If any MANDATORY test fails → file bug, fix before proceeding

**N. Handoff:** verification_report.md → S02, S03

**O. Owner:** Operator. Reviewer: None (self-verified).

**P. Claude Role:** FORBIDDEN

**Q-T. Claude I/O:** N/A

**U. Done Criteria:** All 10 foundational controls pass. Report written.

**V. Stop-State:** If controls fail, fix them before proceeding. Do not skip.

---

### SPRINT S02: DATA GOVERNANCE WIRING

**A. Purpose:** Verify P0/P1/P2 data classification is enforced. No Claude module can write to P0 artifacts.

**B. Scope In:**
- Verify all P0 artifacts have single writer
- Verify /app/data/claude/ namespace exists (create if not)
- Verify no Python file in ouroboros/ writes to /app/config/ except config_writer.py and contract_expander.py
- Verify atomic write discipline (tmp+rename) in config_writer.py

**C. Scope Out:** No new Claude roles.

**D. Dependencies:** S01

**E. Inputs:** Source code, running containers

**F. Systems Touched:**
- `/app/data/claude/` directory creation (in entrypoint.sh)
- Verification of config_writer.py write path

**G. Exact Build Tasks:**
1. `mkdir -p /app/data/claude/{reviews,briefings,challenges,curation,rejected_reviews,anomalies,macro}` — add to entrypoint.sh
2. Grep all Python files for writes to `/app/config/` — verify only config_writer.py and contract_expander.py
3. Verify config_writer.py uses atomic write (write to .tmp then os.rename)
4. Verify contract_expander.py uses atomic write

**H. Deliverables:**
- Updated entrypoint.sh with Claude directory creation
- `tasks/sprint_02/data_governance_audit.md`

**I. Tests:**
- [MANDATORY] `/app/data/claude/` exists after container restart
- [MANDATORY] No Python file outside config_writer/contract_expander writes to /app/config/
- [MANDATORY] config_writer.py write is atomic (tmp+rename)

**J. Validation:** Grep results in audit report

**K. Shadow/Live:** Live (directory creation is safe)

**L. Rollback:** Remove mkdir from entrypoint.sh

**M. Failure Modes:** If non-approved file writes to /app/config/ → fix the file

**N. Handoff:** Updated entrypoint.sh → S03

**O. Owner:** Operator

**P. Claude Role:** FORBIDDEN (subject of governance, not assistant)

**U. Done Criteria:** Audit report clean. Directories created. Entrypoint updated.

---

### SPRINT S03: CLAUDE CLI INSTALL

**A. Purpose:** Install Claude Code CLI on EC2 so all Claude roles can use `claude -p` at $0/month.

**B. Scope In:**
- Install Node.js 22 on EC2
- Install @anthropic-ai/claude-code globally
- Authenticate with Max subscription
- Test `claude -p` returns valid JSON
- Create CLAUDE.md project context file

**C. Scope Out:** No Claude roles activated yet.

**D. Dependencies:** S01 (engine verified stable), S02 (directories exist)

**E. Inputs:** EC2 SSH access, Max subscription credentials

**F. Systems Touched:**
- EC2 system packages (Node.js)
- /app/CLAUDE.md (new file)
- Dockerfile (add Node.js + Claude CLI to container image)

**G. Exact Build Tasks:**
1. SSH to EC2
2. `curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -`
3. `sudo apt-get install -y nodejs`
4. `sudo npm install -g @anthropic-ai/claude-code`
5. `claude login` (one-time OAuth)
6. `claude -p "Return JSON: {\"status\": \"ok\"}" --output-format json` (verify)
7. Create /app/CLAUDE.md with project context, data locations, guardrails
8. Update Dockerfile to bake Node.js + Claude CLI into container image
9. `docker compose build && docker compose up -d` (rebuild with Claude CLI)
10. Verify `claude -p` works inside container

**H. Deliverables:**
- Node.js 22 installed on EC2 (and in Dockerfile)
- Claude CLI installed and authenticated
- /app/CLAUDE.md (100 lines, project context)
- Updated Dockerfile

**I. Tests:**
- [MANDATORY] `claude -p '{"test":true}' --output-format json` returns valid JSON
- [MANDATORY] `claude -p` works inside Docker container
- [MANDATORY] CLAUDE.md exists and is readable

**J. Validation:** JSON response from claude -p

**K. Shadow/Live:** Live (CLI install is safe, no trading impact)

**L. Rollback:** `sudo npm uninstall -g @anthropic-ai/claude-code && sudo apt-get remove nodejs`

**M. Failure Modes:**
- OAuth fails → re-authenticate
- npm install fails → check Node.js version
- Docker build fails → check Dockerfile syntax

**N. Handoff:** Working `claude -p` → S04, S07, S11, S15, S16

**O. Owner:** Operator

**P. Claude Role:** Subject of installation, not assistant

**U. Done Criteria:** `claude -p` returns valid JSON both on host and inside container.

---

### SPRINT S04: CLAUDE HELPER MODULE

**A. Purpose:** Create shared Python utilities for all Claude integration modules.

**B. Scope In:**
- `claude_helper.py`: claude_query(), load_context_files(), send_telegram()
- Retry logic (3 attempts, exponential backoff)
- JSON validation on Claude responses
- Context truncation (H4: max 8,000 tokens)
- Telegram message sending

**C. Scope Out:** No specific Claude roles.

**D. Dependencies:** S03 (Claude CLI working)

**E. Inputs:** Working `claude -p` command

**F. Systems Touched:**
- New file: `/app/python_brain/ouroboros/claude_helper.py` (~120 lines)

**G. Exact Build Tasks:**
1. Create claude_helper.py with claude_query() function
2. Implement subprocess.run() with claude -p
3. Implement 3-retry with exponential backoff (2^attempt seconds)
4. Implement JSON parse validation
5. Implement 120s timeout
6. Implement load_context_files() with 50K char truncation
7. Implement send_telegram() with HTML/Markdown support
8. Add py_compile validation

**H. Deliverables:**
- `/app/python_brain/ouroboros/claude_helper.py` (code in PLAN_2_MERGED_FINAL.md Section 9)

**I. Tests:**
- [MANDATORY] `python3 -c "from python_brain.ouroboros.claude_helper import claude_query"` succeeds
- [MANDATORY] claude_query("Return {\"status\":\"ok\"}") returns dict with status key
- [MANDATORY] claude_query with invalid prompt returns None after 3 retries
- [ADVISORY] send_telegram() delivers to operator chat

**K. Shadow/Live:** Live (no trading impact)

**L. Rollback:** Delete claude_helper.py

**O. Owner:** Operator

**P. Claude Role:** Subject of helper module, not assistant

**U. Done Criteria:** Module importable, claude_query() returns valid JSON, send_telegram() delivers.

---

### SPRINT S05: NIGHTLY PIPELINE SCRIPT (H1)

**A. Purpose:** Replace 6 individual cron entries with one sequential pipeline script to prevent race conditions.

**B. Scope In:**
- Create `/app/scripts/nightly_pipeline.sh`
- Sequential: nightly_v6 → config_writer → win_loss_delta → claude_review → challenger → approval_gate
- `set -euo pipefail` for error handling
- `flock -n /tmp/nightly.lock` to prevent parallel runs
- Logging to /var/log/nightly_pipeline.log

**C. Scope Out:** Claude roles not yet created (placeholder steps that gracefully skip if file missing)

**D. Dependencies:** S01

**F. Systems Touched:**
- New file: `/app/scripts/nightly_pipeline.sh`
- Modified: crontab (replace 6 entries with 1)

**G. Exact Build Tasks:**
1. Create nightly_pipeline.sh (code in PLAN_2_MERGED_FINAL.md H1 section)
2. Add graceful skip for not-yet-created Claude modules: `[ -f script ] && python3 -m script || echo "SKIP: not yet created"`
3. Update crontab: single entry at 04:50 with flock
4. Test: run pipeline manually, verify sequential execution
5. Deploy: rsync + docker compose build + up -d

**H. Deliverables:**
- `/app/scripts/nightly_pipeline.sh`
- Updated crontab

**I. Tests:**
- [MANDATORY] Pipeline runs sequentially (verify via timestamps in log)
- [MANDATORY] flock prevents parallel execution
- [MANDATORY] `set -euo pipefail` causes abort on error
- [MANDATORY] Missing Claude modules gracefully skipped

**L. Rollback:** Restore individual cron entries

**U. Done Criteria:** Pipeline runs end-to-end, sequential, with logging.

---

### SPRINT S06: TOML VALIDATION (H3)

**A. Purpose:** Ensure no corrupt TOML ever reaches the engine via SIGHUP.

**B. Scope In:**
- Add tomllib.loads() validation before every TOML write in config_writer.py
- Add atomic write (write to .tmp, validate, rename)

**D. Dependencies:** S01

**F. Systems Touched:**
- Modified: `python_brain/ouroboros/config_writer.py`

**G. Exact Build Tasks:**
1. In config_writer.py, before writing dynamic_weights.toml:
   - Generate TOML string
   - Parse with `tomllib.loads(content)` — if fails, abort write, alert operator
   - Write to `dynamic_weights.toml.tmp`
   - `os.rename()` to `dynamic_weights.toml` (atomic)
   - Then SIGHUP

**I. Tests:**
- [MANDATORY] Intentionally malformed TOML → write aborted, previous file intact
- [MANDATORY] Valid TOML → write succeeds, SIGHUP sent

**L. Rollback:** Remove validation (unsafe but simple)

**U. Done Criteria:** Zero corrupt TOML writes possible.

---

### SPRINT S07: FORENSIC REVIEW API→CLI

**A. Purpose:** Complete claude_review.py: switch from Anthropic API SDK ($$$) to Claude CLI ($0).

**B. Scope In:**
- Replace `anthropic.Anthropic()` calls with `claude_helper.claude_query()`
- Add gate_vetoes.ndjson to review context
- Add missed_winner_detector output to context
- Add trade classification taxonomy (W1-W5, L1-L7) to system prompt
- Output to /app/data/claude/reviews/review_YYYY-MM-DD.json

**D. Dependencies:** S03, S04

**F. Systems Touched:**
- Modified: `python_brain/ouroboros/claude_review.py` (existing 470-line file)

**G. Exact Build Tasks:**
1. Replace `import anthropic` with `from python_brain.ouroboros.claude_helper import claude_query, load_context_files, send_telegram`
2. Replace API call with claude_query(prompt, output_format="json")
3. Add today's gate_vetoes (top 20 by count) to context
4. Add missed_winners.json to context
5. Add W1-W5, L1-L7 taxonomy to system prompt
6. Write output to /app/data/claude/reviews/review_YYYY-MM-DD.json
7. Send summary to Telegram

**H. Deliverables:**
- Updated claude_review.py
- Example review JSON output

**I. Tests:**
- [MANDATORY] Valid JSON output for 5 consecutive nights
- [MANDATORY] Trade classifications use W1-W5/L1-L7 taxonomy
- [MANDATORY] Gate vetoes included in review
- [MANDATORY] Telegram summary delivered
- [MANDATORY] No `import anthropic` remaining

**K. Shadow/Live:** Live (forensic review is advisory, doesn't affect trading)

**L. Rollback:** Revert to API-based version (git revert)

**P. Claude Role:** PRIMARY ASSISTANT (Role A — Post-Trade Forensic Analyst)
**Q. Claude Reads:** WAL events, gate_vetoes, missed_winners, nightly_output, dynamic_weights, context_store
**R. Claude Produces:** JSON with trade classifications, root causes, gate tuning recs
**S. Stored:** /app/data/claude/reviews/review_YYYY-MM-DD.json
**T. Final Truth Owner:** Operator (via Telegram review)

**U. Done Criteria:** 5 consecutive valid JSON reviews, Telegram delivered, $0 cost.

---

### SPRINT S08-S24: [Detailed specs continue for each sprint]

*Each sprint follows the identical A-V heading structure above. The full detailed specs for S08-S24 will be produced in the continuation of this file.*

---

## PART 4: REQUIRED SPRINT SEQUENCING

```
SEQUENCE LAYER 1: FOUNDATIONS (S01-S02)
  S01: Foundational Verification ─────────────────┐
  S02: Data Governance Wiring ────────────────────┤
                                                   │
SEQUENCE LAYER 2: CLAUDE INFRASTRUCTURE (S03-S06) │
  S03: Claude CLI Install ◄────────────────────────┤
  S04: Claude Helper Module ◄──── S03              │
  S05: Nightly Pipeline (H1) ◄────────────────────┤
  S06: TOML Validation (H3) ◄────────────────────┘

SEQUENCE LAYER 3: CORE CLAUDE ROLES (S07-S12)
  S07: Forensic Review ◄──── S03 + S04
  S08: Challenger ◄──── S07
  S09: Approval Gate ◄──── S08 + S06
  S10: Drift Cap ◄──── S09
  S11: Morning Briefing ◄──── S03 + S04
  S12: Evening Briefing ◄──── S11

SEQUENCE LAYER 4: EXTENDED CLAUDE ROLES (S13-S19)
  S13: Curation Shadow ◄──── S09
  S14: Gate Calibration ◄──── S07
  S15: Anomaly Assessor ◄──── S03 + S04
  S16: Macro Interpreter ◄──── S03 + S04
  S17: SDE Sandbox ◄──── S03
  S18: Psych Audit ◄──── S07
  S19: SEC/RNS Scanner ◄──── S03 + S04

SEQUENCE LAYER 5: VALIDATION + LUXURY (S20-S24)
  S20: Curation Promotion ◄──── S13 + 100 trades
  S21: Alpha Model Shadow ◄──── 200+ trades
  S22: Kelly Shadow ◄──── 200+ trades
  S23: Gemini Fixes Batch 1 ◄──── S01
  S24: Gemini Fixes Batch 2 ◄──── S23 + evidence
```

### Parallelization Plan

**Can run in parallel (no dependencies between them):**
- S03 + S05 + S06 (all depend on S01 only)
- S11 + S14 + S15 + S16 (all depend on S03+S04 only)
- S13 + S14 + S17 + S18 + S19 (all depend on earlier sprints, no cross-dependency)
- S21 + S22 (both depend on 200+ trades, independent of each other)

**Must be sequential:**
- S01 → S02 → S03 → S04 (foundation chain)
- S07 → S08 → S09 → S10 (governance chain)
- S11 → S12 (briefing chain)
- S13 → S20 (curation chain)

---

## PART 5: TESTING FRAMEWORK

### Test Categories Per Sprint

| Test Type | Description | When Required |
|-----------|-------------|---------------|
| Unit | Individual function correctness | Every sprint with new code |
| Integration | End-to-end flow (input → output) | Every sprint |
| Smoke | "Does it start without crashing?" | Every sprint |
| Schema | JSON/TOML output matches expected shape | Claude output sprints |
| Replay | WAL replay produces same state | Hot-path sprints |
| Degraded | System survives when component fails | Claude sprints (fallback) |
| Restart | Container restart doesn't lose state | Hot-path sprints |
| Rollback | Can undo sprint changes cleanly | Every sprint |
| Operator-readability | Human can understand outputs | Briefing sprints |
| Artifact-completeness | All handoff artifacts exist | Every sprint |

### Blocking vs Advisory

- **Hard blockers:** Unit tests, integration tests, smoke tests, schema tests
- **Soft blockers:** Replay tests (block only hot-path sprints)
- **Advisory:** Operator-readability, artifact-completeness (inform quality, don't block)
- **Shadow-only continuation:** If a Claude role fails schema validation, it can continue in shadow mode while the prompt is fixed

---

## PART 6: ROLLBACK AND RECOVERY MATRIX

| Sprint | Rollback Trigger | Rollback Method | Post-Rollback Verify | Operator Action |
|--------|-----------------|-----------------|---------------------|-----------------|
| S01 | N/A (verification only) | N/A | N/A | N/A |
| S02 | Entrypoint breaks container | Remove mkdir from entrypoint.sh | Container starts | Redeploy |
| S03 | Claude CLI breaks container | Remove Node.js from Dockerfile | Container starts without Claude | Redeploy |
| S04 | Helper module import fails | Delete claude_helper.py | No Claude modules run | Redeploy |
| S05 | Pipeline hangs/crashes | Restore individual cron entries | Nightly jobs run independently | Edit crontab |
| S06 | Validation blocks valid TOML | Remove validation check | config_writer writes directly | Edit config_writer.py |
| S07 | Review generates invalid JSON | Revert claude_review.py to API version | Reviews resume via API | git revert + deploy |
| S08 | Challenger blocks valid recs | Disable challenger in pipeline.sh | Config_writer runs directly | Comment out line |
| S09 | Approval gate routes wrong | Disable gate in pipeline.sh | Config_writer runs directly | Comment out line |
| S10 | Drift cap too aggressive | Remove drift check | Gate applies without drift cap | Edit approval_gate.py |
| S11 | Briefing fails to send | Disable cron entry | No briefings (non-critical) | Comment cron |
| S12 | Evening briefing fails | Disable cron entry | No evening briefings | Comment cron |
| S13 | Curation degrades WR >10% | Auto-revert to deterministic | ticker_selector runs alone | Automatic |
| S14 | Gate calibration bad recs | Ignore recommendations | No gate changes | Operator discretion |
| S15-S19 | Any Claude role fails | Disable individual cron entry | System runs without that role | Comment cron |

---

## PART 7: HANDOFF ARTIFACT STANDARD

### Template (used after every sprint)

```markdown
# Sprint SXX Handoff — [Sprint Name]
**Date:** YYYY-MM-DD
**Duration:** Xh
**Owner:** [name]

## Files Changed
- [file path] — [what changed]

## Files Created
- [file path] — [purpose]

## Config Changes
- [config file] — [parameter: old → new]

## Tests Executed
| Test | Type | Result |
|------|------|--------|
| [test name] | [unit/integration/smoke] | PASS/FAIL |

## Replay Evidence
- [WAL replay verified: YES/NO/N/A]

## Operator Notes
- [anything the operator needs to know]

## Known Limitations
- [anything not yet resolved]

## Rollback Instructions
1. [step 1]
2. [step 2]

## Next Sprint Prerequisites
- [what S(XX+1) needs from this sprint]
```

---

## PART 8: PARALLELIZATION PLAN

### Track A: Foundation + Governance (Sequential, Critical Path)
S01 → S02 → S03 → S04 → S07 → S08 → S09 → S10

### Track B: Infrastructure Hardening (Parallel with Track A after S01)
S05 + S06 (can run simultaneously, both depend only on S01)

### Track C: Operator Briefings (Parallel with Track A after S04)
S11 → S12

### Track D: Extended Claude Roles (Parallel, after S03+S04)
S14 + S15 + S16 + S17 + S18 + S19 (all independent, all depend on S03+S04)

### Track E: Shadow Validation (After 100+ trades)
S13 → S20

### Track F: Luxury Layers (After 200+ trades)
S21 + S22

### Merge Authority
- Tracks B, C, D merge into main via git after individual sprint completion
- No cross-track merge conflicts expected (different files)
- Track E and F merge only after evidence gates pass

---

## PART 9: CLAUDE MAX-PLAN INTEGRATION MAP

| Sprint | Claude Used? | Why | Role | Packet Shape | Output Format | Validation | Prevents Execution Truth? | Advisory/Governed? |
|--------|-------------|-----|------|-------------|---------------|------------|--------------------------|-------------------|
| S01 | No | Verification only | Forbidden | N/A | N/A | N/A | N/A | N/A |
| S02 | No | Data governance | Forbidden | N/A | N/A | N/A | N/A | N/A |
| S03 | Subject | CLI installation | Subject | N/A | N/A | N/A | N/A | N/A |
| S04 | Subject | Helper module | Subject | N/A | N/A | N/A | N/A | N/A |
| S05 | No | Pipeline script | Forbidden | N/A | N/A | N/A | N/A | N/A |
| S06 | No | TOML validation | Forbidden | N/A | N/A | N/A | N/A | N/A |
| S07 | Yes | Trade forensics | Primary (A) | WAL+vetoes+context ≤8K tokens | JSON with W1-W5/L1-L7 | json.loads() | Yes — advisory only | Advisory |
| S08 | Yes | Parameter challenge | Primary (B) | nightly_output+weights+context | JSON with APPLY/REJECT per param | json.loads() + bounds check | Yes — gate decides | Governed |
| S09 | Yes | Approval routing | Primary (C) | challenger output + bounds | JSON with decision + audit entry | Bounds validation | Yes — hard bounds enforced | Governed |
| S10 | No | Drift math | Forbidden | N/A | N/A | N/A | N/A | N/A |
| S11 | Yes | Morning briefing | Primary (D) | review+challenger+approval+macro | HTML Telegram message | Telegram API success | Yes — informational only | Advisory |
| S12 | Yes | Evening briefing | Primary (E) | Day's WAL+P&L+vetoes | HTML Telegram message | Telegram API success | Yes — informational only | Advisory |
| S13 | Yes | Universe curation | Shadow (F) | Scanner+Thompson+Ouroboros | JSON top-100 list | Comparison logged | Yes — deterministic wins | Shadow |
| S14 | Yes | Gate calibration | Primary (G) | gate_vetoes weekly aggregate | JSON per-gate analysis | json.loads() | Yes — operator approval needed | Advisory |
| S15 | Yes | Anomaly assessment | Primary (H) | Anomaly trigger data | JSON severity+recommendation | json.loads() | Yes — engine decides | Advisory |
| S16 | Yes | Macro intelligence | Primary (I) | Calendar+positions+VIX | JSON impact+blackout rec | json.loads() | Yes — ≤60min auto only | Governed |
| S17 | Research | SDE script writing | Research only | Scenario description | Python script | Human review | Yes — sandbox only | Research |
| S18 | Yes | Psych audit | Primary | WAL interventions | JSON counterfactual | json.loads() | Yes — advisory | Advisory |
| S19 | Yes | Filing diffs | Primary | SEC/RNS filings | JSON semantic delta | json.loads() | Yes — advisory | Shadow |

---

## PART 10: SPRINT-BY-SPRINT CONTINUOUS PLAN

### S01 → S02: Foundation Verified → Data Governed
**Now true:** All 10 foundational controls pass. Data ownership map verified.
**Proven:** Engine is stable, restartable, config-safe.
**Remaining risk:** Claude CLI not yet installed.
**Next consumes:** EC2 access for Node.js install.
**Frozen:** Nothing (no changes made yet).

### S02 → S03: Data Governed → Claude CLI Ready
**Now true:** Directories exist. Ownership map enforced.
**Proven:** No P0 artifact exposed to Claude.
**Next consumes:** Working `claude -p` command.
**Frozen:** Data governance rules.

### S03 → S04: CLI Ready → Helper Module Ready
**Now true:** Claude CLI works on host and in container.
**Proven:** $0/month cost model works.
**Next consumes:** Importable claude_helper.py module.
**Frozen:** Claude CLI version and authentication.

### S04 → S07: Helper Ready → Forensic Review Live
**Now true:** claude_query() works with retry and JSON validation.
**Proven:** Claude integration plumbing works end-to-end.
**Next consumes:** claude_review.py producing nightly analysis.
**Monitor:** JSON parse success rate (must be 100% over 5 nights).

### S07 → S08: Forensic Review → Challenger Live
**Now true:** Nightly forensic review generates trade classifications.
**Proven:** Claude can analyze trade data and produce structured output.
**Next consumes:** Ouroboros recommendations challenged by Claude.
**Monitor:** Review quality, classification accuracy.

### S08 → S09: Challenger → Approval Gate
**Now true:** Claude challenges Ouroboros recommendations with statistical rigor.
**Proven:** Claude catches weak recommendations.
**Next consumes:** Governed config changes through approval gate.
**Monitor:** Challenge quality, false rejection rate.

### S09 → S10: Approval Gate → Drift Cap
**Now true:** All config changes go through APPLY/REJECT/TEST_ONLY/NEEDS_DATA pipeline.
**Proven:** Hard bounds enforced, audit trail complete.
**Next consumes:** 30-day baseline tracking prevents slow drift.
**Frozen:** Hard bounds (kelly_fraction [0.10, 0.35], etc.)

### S10 → Full Intelligence Stack
**Now true:** Complete governed learning pipeline: Ouroboros → Challenger → Gate → Drift Cap → Config.
**Proven:** Parameter changes are evidence-gated, bounded, logged, reversible.
**Remaining:** Extended roles (curation, gate calibration, anomaly, macro, SDE).
**Monitor:** Parameter drift, WR trajectory, PF trajectory.

---

## PART 11: FINAL IMPLEMENTATION VERDICT

**Is the plan implementation-ready?** Yes. The canonical merged file provides all architecture, data flows, file paths, function names, and guardrails. This roadmap converts it into 24 executable sprints.

**First 5 build sprints (in order):**
1. **S01: Foundational Verification** (1h) — verify everything works
2. **S03: Claude CLI Install** (2h) — prerequisite for all Claude roles
3. **S05 + S06: Pipeline + TOML Validation** (2h, parallel) — hardening
4. **S04: Claude Helper Module** (1h) — shared utilities
5. **S07: Forensic Review API→CLI** (4h) — highest-ROI Claude integration

**What should NOT be built yet:**
- S21: Alpha Model Shadow (needs 200+ trades)
- S22: Kelly Shadow (needs 200+ trades)
- S24: Gemini Fixes Batch 2 (needs evidence)
- Polygon/FMP integration (needs cost-benefit proof)
- Level 2 sniper (needs L2 subscription wiring)

**Biggest execution risks:**
1. Claude CLI authentication expires on EC2 → re-auth protocol needed
2. Docker image size bloat from Node.js → monitor, prune cache
3. Nightly batch window exceeds 40 minutes → upgrade EC2 if needed
4. IB Gateway 2FA weekly re-auth → operator must monitor

**Biggest false-complexity risks:**
1. Over-engineering Claude prompts (keep simple, iterate)
2. Premature alpha model unification (let current strategies prove themselves)
3. Adding more gates before calibrating existing 30

**Highest-ROI build order:**
1. Claude forensic review (S07) — post-cost truth illumination
2. Approval gate (S09) — governed parameter changes
3. Morning/evening briefings (S11-S12) — operator awareness
4. Gate calibration (S14) — improve existing gates with evidence

**What a disciplined team should do next, in order:**
1. Execute S01 (verify foundations) — 1 hour
2. Execute S03 (install Claude CLI) — 2 hours
3. Execute S05 + S06 in parallel (pipeline + TOML validation) — 2 hours
4. Execute S04 (helper module) — 1 hour
5. Execute S07 (forensic review) — 4 hours
6. Let the engine trade for 1 week with forensic reviews running
7. Execute S08 → S09 → S10 (governance chain) — 8 hours
8. Execute S11 + S12 (briefings) — 3 hours
9. Collect 100+ trades before touching S13 (curation shadow)
10. Markets open Monday. Start S01 now.

---

**This roadmap is one continuous build march. Each sprint feeds the next. Evidence gates prevent premature promotion. Claude is governed at every step. Rust owns execution. Operator owns authority. Document is build-ready.**
