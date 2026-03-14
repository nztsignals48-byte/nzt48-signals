# EXECUTION_COMMAND_V1.md

**Created:** 2026-03-08
**Purpose:** Triaged execution command for NZT-48 sterilization phase
**Status:** Ready for deployment

---

## Triage Summary

### What Gemini Got Right ✓
1. **State Management** — EXECUTION_STATE.md as persistent memory across context compactions is CRITICAL
2. **One Ticket Rule** — Batching fixes is how bugs compound. Serial execution with commits prevents catastrophic regressions
3. **Strict State Machine** — PLAN → IMPLEMENT → TEST → COMMIT → VERIFY → NEXT prevents Ralph Wiggum loops
4. **Rollback Points** — Git SHA tracking before each fix enables instant recovery
5. **Terminal Marker** — `<promise>` tag signals completion without ambiguity
6. **Phase Separation** — Sterilization (fix contradictions) must complete before greenfield (new features)

### What to Adopt from ChatGPT
1. **Truth Table Mindset** — For each ticket, verify the logical consistency before and after the fix
2. **Critical Path Focus** — The 15 tickets target the highest-severity conflicts from V2 audit Section H
3. **No Trust Fallbacks** — When fixing contradictions, the fix must ELIMINATE the contradiction, not paper over it

### What to Reject
1. ❌ ChatGPT's "audit → fix → re-audit" loop (infinite context collapse)
2. ❌ Conflicting directives ("never trust fallbacks" vs "implement fixes")
3. ❌ Gemini's overly verbose prompt structure (1,000+ line commands are hard to execute)

---

## EXECUTION COMMAND

Copy the block below and paste it into Claude Code to begin sterilization.

```text
<task>
You are entering EXECUTION MODE for NZT-48 sterilization.

MISSION: Fix 15 critical contradictions identified in AUDIT_REPORT_V2.md Section H.
CONSTRAINT: ONE ticket at a time. NO batching. NO refactoring beyond the minimal fix.
TERMINATION: Output <promise>EXECUTION-PHASE-COMPLETE</promise> when all 15 tickets are COMPLETED or SKIPPED.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 0: INITIALIZE STATE TRACKING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Create /Users/rr/nzt48-signals/tasks/EXECUTION_STATE.md with this exact schema:

# EXECUTION STATE

**Phase:** STERILIZATION (Phase 1)
**Started:** 2026-03-08
**Current Ticket:** NONE
**Status:** INITIALIZED

## Ticket Queue (Priority Order)

- [ ] TICK-01: TickLoop silent failure → FATAL (H-03, TG-03)
- [ ] TICK-02: InvariantEnforcer → actually schedule + boot check (H-02, TG-02)
- [ ] TICK-03: Weekly halt unification 6% everywhere (H-04, TG-07)
- [ ] TICK-04: Max positions unification 3 everywhere (H-05, TG-07)
- [ ] TICK-05: Confidence floor unification 65 everywhere (H-13, TG-06)
- [ ] TICK-06: Data age check (not fetch latency) (H-10, TG-04)
- [ ] TICK-07: S16 ISA SHORT guard (H-11, TG-08)
- [ ] TICK-08: Redis volatile-lru (H-07, TG-09)
- [ ] TICK-09: ImmutableRiskRules metaclass guard (H-06)
- [ ] TICK-10: Transition buffer decrement (H-09, TG-13)
- [ ] TICK-11: Overnight kill ALL ETPs (H-14, TG-15)
- [ ] TICK-12: Kill switch deactivate unification (H-12, TG-16)
- [ ] TICK-13: ProfitLadder atr=0.0 fix (H-25)
- [ ] TICK-14: ThresholdRegistry enforcement (H-23, TG-14)
- [ ] TICK-15: Drift detector phantom file fix (drift_report.json → drift_reports.jsonl)

## Completed Tickets
(none yet)

## Failed/Skipped Tickets
(none yet)

## Rollback Points
(git SHA recorded before each fix)

## Notes
- Each ticket follows: PLAN → IMPLEMENT → TEST → COMMIT → VERIFY → NEXT
- If TEST fails → ROLLBACK and document in Failed/Skipped
- NEVER batch fixes. ONE ticket at a time.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STATE MACHINE (Repeat for EACH ticket)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For TICK-01 through TICK-15, execute this loop:

┌─────────────────────────────────────────────────────────────────┐
│ 1. PLAN                                                         │
│    - Read AUDIT_REPORT_V2.md Section H for this ticket         │
│    - Grep/Read the relevant files                               │
│    - Identify EXACT lines to change                             │
│    - Update EXECUTION_STATE.md:                                 │
│      * Current Ticket: TICK-XX                                  │
│      * Status: PLAN                                             │
│    - Write plan summary in 3-5 lines                            │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. IMPLEMENT                                                    │
│    - Record git SHA: git rev-parse HEAD                         │
│    - Add SHA to EXECUTION_STATE.md Rollback Points              │
│    - Make the change using Edit tool                            │
│    - CONSTRAINT: < 20 lines changed per ticket                  │
│    - CONSTRAINT: NO refactoring outside ticket scope            │
│    - Update EXECUTION_STATE.md Status: IMPLEMENT                │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. TEST                                                         │
│    - Run: python -c "import main" (smoke test)                  │
│    - If pytest exists for this module, run it                   │
│    - Update EXECUTION_STATE.md Status: TEST                     │
│    - If FAIL → goto ROLLBACK                                    │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. COMMIT                                                       │
│    - git add [specific files only]                              │
│    - git commit -m "TICK-XX: [description]"                     │
│    - Update EXECUTION_STATE.md Status: COMMIT                   │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 5. VERIFY                                                       │
│    - Grep to confirm the fix is in place                        │
│    - Check no regressions introduced                            │
│    - Update EXECUTION_STATE.md Status: VERIFY                   │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 6. NEXT                                                         │
│    - Mark ticket as [x] in EXECUTION_STATE.md                   │
│    - Add to Completed Tickets section                           │
│    - Update Current Ticket to next in queue                     │
│    - Update Status: INITIALIZED (for next ticket)               │
│    - Repeat from step 1 for next ticket                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ ROLLBACK (if TEST fails)                                        │
│    - Run: git checkout -- [changed files]                       │
│    - Document failure in EXECUTION_STATE.md:                    │
│      * Add to Failed/Skipped section                            │
│      * Include reason for failure                               │
│    - Move to next ticket                                        │
└─────────────────────────────────────────────────────────────────┘


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TICKET SPECIFICATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TICK-01: TickLoop silent failure → FATAL
  Files: main.py:9222-9225
  Issue: TickLoop failure caught with try/except that logs WARNING and continues. System opens ZERO trades if TickLoop dies silently.
  Fix: Change `logger.warning` to `logger.critical`. Set kill switch. Send Telegram alert. Do NOT silently continue.
  Reference: AUDIT_REPORT_V2.md H-03, TG-03

TICK-02: InvariantEnforcer → actually schedule + boot check
  Files: main.py:1284-1296 (init), main.py:1329-1336 (_run_invariant_runtime_check), core/invariant_enforcer.py
  Issue: `_run_invariant_runtime_check()` defined at main.py:1329 but NEVER added to scheduler (searched all 66 add_job calls). `enforce_boot()` NEVER CALLED. Init failure labeled "non-critical" with blanket except at main.py:1295-1296.
  Fix: (1) Add `scheduler.add_job(_run_invariant_runtime_check, 'interval', seconds=60)` in setup_scheduler(). (2) Call `self.invariant_enforcer.enforce_boot()` during startup. (3) Change "non-critical" except to re-raise or sys.exit(1).
  Reference: AUDIT_REPORT_V2.md H-02, TG-02

TICK-03: Weekly halt unification 6% everywhere
  Files: qualification/circuit_breakers.py:120 (_WEEKLY_DD_HALT = 0.08), qualification/risk_sizer.py:52 (MAX_WEEKLY_LOSS = 0.06)
  Issue: risk_sizer says 6%, circuit_breakers says 8%. ImmutableRiskRules (constitutional) says 6%.
  Fix: Change `circuit_breakers.py:120` from `_WEEKLY_DD_HALT = 0.08` to `_WEEKLY_DD_HALT = 0.06`. Constitution wins.
  Reference: AUDIT_REPORT_V2.md H-04, TG-07

TICK-04: Max positions unification 3 everywhere
  Files: qualification/risk_sizer.py:53 (=3), config/settings.yaml:623 (=4), command_center/tick_loop.py:286 (=2), core/threshold_registry.py (=3), strategies/universal_scanner.py (=5)
  Issue: FIVE different values across codebase: 2, 3, 3, 4, 5. ImmutableRiskRules says 3.
  Fix: Change tick_loop.py:286 from 2→3. Change settings.yaml:623 from 4→3. Change universal_scanner.py from 5→3. Constitution (risk_sizer=3) wins.
  Reference: AUDIT_REPORT_V2.md H-05, TG-07

TICK-05: Confidence floor unification 65 everywhere
  Files: strategies/daily_target.py:75 (=65), qualification/risk_sizer.py:57 (=65), uk_isa/gate_diagnostics.py:62 (=55), main.py:2973 (=60), main.py:4842 (=55 via universal_scanner), strategies/universal_scanner.py:80 (=58)
  Issue: 4+ different confidence floors: 55, 58, 60, 65. Constitutional value = 65.
  Fix: Change gate_diagnostics.py:62 from 55→65. Change main.py:2973 from 60→65. Change universal_scanner.py:80 from 58→65.
  Reference: AUDIT_REPORT_V2.md H-13, TG-06

TICK-06: Data age check (not fetch latency)
  Files: command_center/tick_loop.py:930-940
  Issue: Staleness gate at tick_loop.py:937 checks FETCH LATENCY (how long the HTTP call took), NOT DATA AGE (how old the last bar timestamp is). System can trade on 15-min-old yfinance data.
  Fix: After fetch, check `data.index[-1]` vs `datetime.now(tz=timezone.utc)`. Reject if age > 120 seconds during active LSE session.
  Reference: AUDIT_REPORT_V2.md H-10, TG-04

TICK-07: S16 ISA SHORT guard
  Files: main.py:4841-4845 (_check_s16_gauntlet), strategies/universal_scanner.py:473,527,579,635,692
  Issue: S15 has ISA SHORT guard at daily_target.py:1110-1116, but S16 (universal_scanner) has NO such guard. UK ISA legally cannot short-sell.
  Fix: Add SHORT rejection for .L tickers at entry of _check_s16_gauntlet() or in universal_scanner signal generation.
  Reference: AUDIT_REPORT_V2.md H-11, TG-08

TICK-08: Redis volatile-lru
  Files: docker-compose.yml:98
  Issue: `--maxmemory-policy noeviction` means Redis refuses ALL writes when 400MB is hit. Plan says allkeys-lru (wrong — evicts position state). Correct answer = volatile-lru.
  Fix: Change `--maxmemory-policy noeviction` to `--maxmemory-policy volatile-lru`. Add TTL to telemetry keys in DB 1.
  Reference: AUDIT_REPORT_V2.md H-07, TG-09

TICK-09: ImmutableRiskRules metaclass guard
  Files: qualification/risk_sizer.py:31-94
  Issue: Instance-level `__setattr__` guard works correctly. But NO metaclass protects class-level attributes. Any module can do `ImmutableRiskRules.RISK_PER_TRADE = 0.05` and silently corrupt constitutional rules.
  Fix: Add a metaclass with `__setattr__` that raises `AttributeError` on class-level mutation after class creation.
  Reference: AUDIT_REPORT_V2.md H-06

TICK-10: Transition buffer decrement
  Files: feeds/regime_classifier.py:500
  Issue: `decrement_transition_buffer()` is DEFINED at line 500 but NEVER CALLED from anywhere in the codebase. Regime changes are instant with zero confirmation, causing 10-20 flaps/day at threshold boundaries.
  Fix: Call `decrement_transition_buffer()` at end of each regime evaluation cycle in `classify()` or wherever regime is assessed.
  Reference: AUDIT_REPORT_V2.md H-09, TG-13

TICK-11: Overnight kill ALL ETPs
  Files: execution/virtual_trader.py:1524-1547, uk_isa/isa_universe.py:139-140
  Issue: Code only enforces overnight_kill=True for 5x products (QQQ5.L, SP5L.L). Constitution R5 mandates ALL leveraged ETPs closed. 3x products can hold overnight with up to 9% gap risk.
  Fix: Set overnight_kill=True for ALL leveraged ETPs in isa_universe.py, not just 5x.
  Reference: AUDIT_REPORT_V2.md H-14, TG-15

TICK-12: Kill switch deactivate unification
  Files: delivery/telegram_bot.py:1854-1863
  Issue: `KillSwitch.deactivate()` only clears the file (`data/KILL_SWITCH`) but does NOT clear the Redis kill state (`nzt:kill` hash). Dual-persistence desync.
  Fix: Add `state_manager.clear_kill()` or Redis DEL to `deactivate()` method so both file AND Redis are cleared.
  Reference: AUDIT_REPORT_V2.md H-12, TG-16

TICK-13: ProfitLadder atr=0.0 fix
  Files: main.py:7117-7118
  Issue: `self.profit_ladder.evaluate(position, current_price, atr=0.0)` — ATR is always 0.0. Trailing stop calculations at rungs 6-7 produce `current_price` as stop (zero trail distance). Stops never trail.
  Fix: Pass actual ATR value from position or ticker data instead of hardcoded 0.0.
  Reference: AUDIT_REPORT_V2.md H-25

TICK-14: ThresholdRegistry enforcement (SKIP candidate — large scope)
  Files: core/threshold_registry.py:76+, ALL modules with hardcoded thresholds
  Issue: ThresholdRegistry is `@dataclass(frozen=True)` singleton used by ZERO production modules. Every module hardcodes its own thresholds. Blood Oath #1 violated by all consumers.
  Fix: This is a >20 line change across 10+ files. SKIP for sterilization phase. Document as Phase 2 item. Boot-time assertion check is a viable minimal fix.
  Reference: AUDIT_REPORT_V2.md H-23, TG-14. NOTE: Likely SKIP due to scope.

TICK-15: Drift detector phantom file fix
  Files: main.py:2167, learning/drift.py:195-199, learning/drift_detector.py
  Issue: main.py:2167 reads `data/drift_report.json` to set drift_defensive=True. But NOTHING writes that file. learning/drift.py:199 writes to `data/drift_reports.jsonl` (plural, JSONL format). File path mismatch = drift defensive mode NEVER triggers.
  Fix: Change main.py:2167 from `data/drift_report.json` to `data/drift_reports.jsonl` and parse as JSONL (read last line, json.loads).
  Reference: AUDIT_REPORT_V2.md V2 Finding 4, DriftDetector section


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After all 15 tickets are COMPLETED or SKIPPED:

1. Run full smoke test:
   python -c "import main"

2. Verify docker build succeeds:
   cd /Users/rr/nzt48-signals
   docker compose build nzt48

3. Update EXECUTION_STATE.md:
   - Status: COMPLETE
   - Summary: X completed, Y skipped/failed

4. Output terminal marker:
   <promise>EXECUTION-PHASE-COMPLETE</promise>


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONSTRAINTS (BLOOD OATH)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. ONE TICKET AT A TIME — Never batch. Never parallelize.
2. NO REFACTORING — Only change what the ticket specifies.
3. < 20 LINES PER TICKET — If more, split into sub-tickets.
4. ROLLBACK ON FAIL — Never push broken code forward.
5. UPDATE STATE AFTER EVERY STEP — EXECUTION_STATE.md is source of truth.
6. NO GREENFIELD — Phase 1 is sterilization only. No new features.
7. NO CONTEXT COLLAPSE — If you hit token limit, read EXECUTION_STATE.md to resume.
8. MINIMAL DIFF — Smallest possible change to fix the contradiction.

If unsure about a fix:
- SKIP the ticket
- Document why in Failed/Skipped section
- Move to next ticket
- DO NOT guess or implement partial fix


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BEGIN EXECUTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Start with STEP 0 (Initialize EXECUTION_STATE.md).
Then proceed to TICK-01.
Follow the state machine for each ticket.
Terminate with <promise>EXECUTION-PHASE-COMPLETE</promise> when done.

</task>
```

---

## Usage Instructions

1. **Copy the entire `<task>` block** above
2. **Paste into Claude Code** as a new message
3. **Do not interrupt** — let Claude execute all 15 tickets serially
4. **Monitor EXECUTION_STATE.md** to track progress
5. **Wait for `<promise>EXECUTION-PHASE-COMPLETE</promise>`** before proceeding to Phase 2

---

## Success Criteria

- ✅ EXECUTION_STATE.md exists and tracks all 15 tickets
- ✅ Each completed ticket has a git commit
- ✅ Each failed ticket is documented with reason
- ✅ `python -c "import main"` succeeds
- ✅ `docker compose build nzt48` succeeds
- ✅ Terminal marker `<promise>EXECUTION-PHASE-COMPLETE</promise>` appears

---

## What Happens After Completion

Once sterilization is complete:
1. Review EXECUTION_STATE.md for any failed/skipped tickets
2. Manually inspect those tickets (may require deeper investigation)
3. Proceed to **Phase 2: Greenfield** (T-01 through T-08 from AEGIS_MASTER_PLAN)

DO NOT mix sterilization and greenfield. Contradictions first, features second.

---

**END OF EXECUTION_COMMAND_V1.md**
