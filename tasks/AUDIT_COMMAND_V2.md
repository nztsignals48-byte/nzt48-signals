# NZT-48/AEGIS FULL-SPECTRUM INSTITUTIONAL AUDIT — V2 COMMAND

## Improvements over V1:
- Every claim must cite `file:line` — NO assumptions from prior analysis
- Grep for ALL threshold values across ALL files (not just known locations)
- Trace execution paths exhaustively (signal → queue → execution → fill)
- Test immutability/invariant claims by checking for violation paths
- Check for "defined but never called" functions
- Classify learning components with harsher criteria
- Cross-reference ALL plan documents against ALL code files
- Verify corrections from V1 (ORANGE CB=0.50, F&G=CNN, weekly halt=6%+8%)

## Command:

```
<task>
You are performing a V2 FULL-SPECTRUM INSTITUTIONAL + ACADEMIC SYSTEM AUDIT of the NZT-48/AEGIS automated trading system at /Users/rr/nzt48-signals/.

CONTEXT: V1 audit exists at tasks/AUDIT_REPORT.md (881 lines). Read it first. Your job is to DEEPEN, CORRECT, and EXTEND the V1 findings. Do NOT simply repeat V1 — add NEW evidence, find NEW bugs, verify V1 claims, and upgrade the report.

ATTACK FROM 4 PERSONAS:
1. Chief Quant — mathematical validity, statistical significance, alpha decay
2. Lead Systems Architect — state machines, race conditions, failure modes
3. Chief Risk Officer — drawdown paths, correlation blowups, regulatory compliance
4. Academic Reviewer — citation validity, methodology rigor, reproducibility

READ THESE FILES (in parallel via subagents):
- main.py (~9300 lines) — the orchestrator
- strategies/daily_target.py — S15
- strategies/universal_scanner.py — S16
- execution/virtual_trader.py — paper trading
- command_center/tick_loop.py — position opener
- qualification/circuit_breakers.py — 8 breakers
- qualification/risk_sizer.py — constitutional rules
- qualification/dynamic_sizer.py — Kelly
- qualification/profit_ladder.py — second profit ladder
- qualification/qualifier.py — 18-gate pipeline
- core/ml_meta_model.py — ML (disabled)
- core/chandelier_exit.py — trailing stops
- core/cross_asset_macro.py — macro signals
- core/trading_discipline.py — discipline engine
- core/threshold_registry.py — ThresholdRegistry
- core/invariant_enforcer.py — runtime invariants
- core/state_manager.py — Redis state
- core/clock.py — time source
- feeds/regime_classifier.py — regime
- feeds/hmm_regime_overlay.py — HMM
- learning/incremental_learner.py — PA classifier
- learning/learning_engine.py — unified learning
- uk_isa/gate_diagnostics.py — diagnostics
- config/settings.yaml — all params
- docker-compose.yml — infrastructure
- aegis/ directory — all plan documents

FOR EACH SECTION, you must:
1. Read V1's finding
2. Verify it against actual code (grep + read)
3. Find NEW issues V1 missed
4. Upgrade severity if warranted
5. Write the improved section to tasks/AUDIT_REPORT_V2.md

PRODUCE THESE 11 SECTIONS + 2 ADDENDA:

A. Executive Verdict — RED/AMBER/GREEN with Tier 1 + Tier 2 findings. MUST correct any V1 errors.
B. Full System Wiring Check — 25+ subsystems. For each: wired YES/PARTIAL/NO, file:line evidence, blast radius.
C. Command Tree Audit — Who writes state? Who opens positions? Race conditions? Authority conflicts?
D. Execution Timing Audit — Exact latency measurements. Structural lateness proof. Recommended cadence.
E. Self-Learning System Audit — 11 components. REAL/PARTIAL/THEATRE/INVALID classification.
F. Academic Research Check — Every cited paper vs actual implementation. Correctly applied / Overstated / Unsupported.
G. Plan vs Code Truth Table — 30+ rows. VERIFIED/PARTIAL/CONTRADICTION. Grep EVERY threshold across ALL files.
H. Stop-Ship Items — Top 25 blockers. P0/P1. Blast radius. Fix. Acceptance test.
I. Timing/Gating Triage — Sprint-ready cards. Priority, effort, dependencies, files, acceptance criteria.
J. Plan Patches — Exact text changes to bring plan in line with code reality.
K. Minimum Viable System — Checklist of what must be true before 100-trade validation gate.

ADDENDUM 1: Critical Questions — 15+ questions the system cannot answer but must.
ADDENDUM 2: IF I HAD 8 HOURS TODAY — Hour-by-hour plan with specific files and acceptance tests.

NEW V2 REQUIREMENTS:
- For Section G, run `grep -rn` for EVERY threshold value (confidence, RVOL, ADX, weekly halt, max positions, etc.) across the ENTIRE codebase. Report ALL locations, not just the ones V1 found.
- For Section C, trace the COMPLETE execution path from signal generation to virtual fill. Document every function call in the chain.
- For Section E, check if any learning component output is actually READ by any decision-making module (not just written).
- For Section H, check if any V1 stop-ship items have actually been fixed since V1 was written.
- Add a "V1 CORRECTIONS" subsection to Section A documenting any V1 errors found.
- Add a "NEW FINDINGS" subsection to each section documenting issues V1 missed.

Write the complete report to tasks/AUDIT_REPORT_V2.md
Generate PDF to tasks/AUDIT_REPORT_V2.pdf using PyMuPDF (fitz.Story pattern)
</task>

<promise>AUDIT_V2_COMPLETE_ALL_SECTIONS_VERIFIED</promise>
<max_iterations>20</max_iterations>
```
