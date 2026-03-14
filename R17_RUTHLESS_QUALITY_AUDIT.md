# R17 RUTHLESS QUALITY AUDIT — AEGIS MASTER PLAN v13.14
## 4-Persona Kill-or-Keep Verdict on EVERY Section

**Date**: 2026-03-06
**Auditor**: Claude Opus 4.6
**Method**: Full plan read (7850+ lines) × full codebase read (6 critical modules) × 4-persona filter
**Directive**: Remove everything that doesn't make the system stronger. Zero bloat tolerance.

---

## PERSONAS APPLIED

- **P1 — Chief Quant** (30y, $2B fund): Does this rule generate alpha or protect capital with mathematical precision?
- **P2 — Lead Systems Architect** (HFT): Is this implementable, atomic, and testable? Or is it hand-waving?
- **P3 — Chief Risk Officer** (ex-market maker): Does this rule actually fire when needed? Or is it dead code?
- **P4 — Fund Manager** (Execution): Does this make money, or is it bureaucratic overhead?

---

## PART 1: R16 ADDITIONS — KILL OR KEEP

### GAP-01: Constitutional Reconciliation (L1/L2/L3 vs Drawdown Cascade)
**VERDICT: KEEP but REWRITE**

All 4 personas agree this reconciliation is necessary. The problem is real — settings.yaml has daily loss at 3%, plan says 2%, Constitution says L1(-1.5%)/L2(-2.5%)/L3(-4%), and the drawdown recovery cascade uses different thresholds entirely.

**BUT**: The current reconciliation creates a 10-row table with TWO parallel systems (intraday + accumulated) that are confusing and likely to cause implementation bugs.

**FIX**: Collapse to ONE unified system. The code already uses 3 levels (circuit_breakers.py lines 42-45: YELLOW -1.5%, ORANGE -2.5%, RED -4.0%). These ARE the Constitution's L1/L2/L3. The accumulated drawdown cascade (YELLOW -3%, ORANGE -4%, RED -5%) is SEPARATE and tracks equity vs HWM, not daily P&L. Keep both but make the distinction crystal clear and remove duplicate rows.

### GAP-02: R19 Partial Exit Amendment
**VERDICT: KEEP — essential**

P1 (Quant): The profit ladder's geometric mean optimisation (33/67 split at §4.4) is the mathematical core of the strategy. R19's "full exit on target" would destroy the ladder. This amendment is correct.
P4 (Fund Manager): Without this amendment, you can't run the profit ladder. Non-negotiable.

### GAP-03: Parameter Drift Limit (15% not 20%)
**VERDICT: KEEP — one-line fix, aligns with Constitution**

Trivial correction. 15% aligns with R23. No downside.

### GAP-04: R4 Total Deployment Cap (40%)
**VERDICT: KEEP but VERIFY against settings.yaml**

P3 (CRO): 40% aggregate deployment cap is correct institutional practice. settings.yaml has max concurrent positions = 3 and max single position = 5% equity (line 193 of dynamic_sizer.py). 3 × 5% = 15% max, well under 40%. So this cap is non-binding at current scale but essential for Phase C multi-position.

**CONCERN**: At £10K with 0.75% risk per trade and 3 positions, notional deployment is roughly 3 × (0.75% × equity / stop_distance) ≈ 3 × £500 = £1,500 = 15%. The 40% cap only becomes binding at higher equity or wider stops. Keep but note it's not binding now.

### GAP-05: Weekly -8% and Monthly -15% Breakers
**VERDICT: KEEP — fills genuine gap**

P3 (CRO): settings.yaml has weekly loss at -6% (line 621). The Constitution says -8%. These DON'T conflict — -6% is a WARNING (reduce), -8% is a HALT. Monthly -15% fills a genuine gap. Keep.

**BUT REMOVE** the verbose "written approval memo before restart" language. In a one-person operation, this is bureaucratic theater. Replace with: "Full review of all trades required before restart."

### GAP-06: Per-Ticker Volatility Regime Layer
**VERDICT: CUT — aspirational complexity**

P2 (Architect): This is 100% unimplemented. The regime_classifier.py has 8 market states. Adding a 5-state per-ticker vol regime creates 8 × 5 = 40 state combinations. There's no code for this, no data to calibrate it, and it adds complexity without proven alpha.

P1 (Quant): At 413 trades, we can't even properly calibrate the 8-state market regime (need 30 trades per regime × 8 = 240 minimum). Adding another dimension is statistical suicide.

P4 (Fund Manager): This doesn't make money. Cut it.

**ACTION**: Remove entirely from §6D. The contradiction detection rules (C1-C5 from GPT-79) remain — they work with the existing 8-state regime.

### GAP-07: Regime Transition Action Matrix (28 from/to pairs)
**VERDICT: TRIM — keep reference, remove bloat**

P2 (Architect): The 5 key transitions listed are genuinely useful operational rules. But "referencing a 28-entry matrix in an archive document" is a recipe for staleness. Nobody will maintain two documents.

P4 (Fund Manager): The 5 key transitions are the only ones that matter at 1 trade/day:
1. Any → SHOCK = EMERGENCY FLATTEN
2. Any → RISK_OFF = FLATTEN, cash
3. RISK_OFF → NORMAL = 0.25x for 30 min
4. SHOCK → NORMAL = 0.25x for 60 min
5. TRENDING_UP → TRENDING_DOWN = FLATTEN longs

**FIX**: Keep these 5 transitions inline. Remove the reference to "28+ entry matrix in archive doc." That matrix doesn't exist in code and never will at 1 trade/day with 12 tickers.

### GAP-08: Failure Simulation Drills Phase
**VERDICT: KEEP — operationally essential**

P2 (Architect): Testing kill switch, recovery, and rollback before live trading is non-negotiable. These 6 drills are concrete and testable.

P3 (CRO): The "any drill failure resets the 14-day G4 clock" rule is correct — you don't go live if you can't survive a network failure.

**MINOR CUT**: Remove "Practice the LIMITED LIVE cutover procedure (dry run, no live capital)" — this is the same as the Go-Live Gate itself, circular.

### GAP-09: G7 Drawdown Recovery Gate
**VERDICT: KEEP**

P3 (CRO): Proving the system can recover from a drawdown without manual intervention is a legitimate go-live criterion. Simple, testable.

### GAP-10: G9 PDF Consistency Gate
**VERDICT: CUT — unimplementable bureaucracy**

P2 (Architect): "Zero contradictions between PDF1 and PDF2 in last 7 days" — what constitutes a "contradiction"? How is it measured? There's no automated way to detect whether "PDF1 recommends action that PDF2's risk assessment would prohibit." This requires human judgment on every PDF, which is exactly the kind of process that will be ignored.

P4 (Fund Manager): PDFs are reporting tools, not control gates. If the system's internal state is consistent, the PDFs will be consistent. This gate adds nothing.

**ACTION**: Remove G9.

### GAP-11: Rollback Procedure
**VERDICT: KEEP — trim to essentials**

P2 (Architect): A 6-step rollback procedure is operationally essential. But "git checkout <commit_hash> -- config/settings.yaml" is already standard DevOps.

**TRIM**: Remove the verbose explanation. Keep the 6 steps as a checklist. Add one line: "CRITICAL: Evidence preservation (step 2) MUST occur before any restart."

### GAP-12: Escalation Matrix
**VERDICT: CUT — one-person operation theater**

P4 (Fund Manager): This is a one-person operation with automated trading. An "escalation matrix" with "IC notification within 15 min" and "Self-review memo" is bureaucratic theater. The system either trades or it doesn't. The kill switch either fires or it doesn't.

P3 (CRO): Replace the entire escalation matrix with: "SEVERITY: LOW = log and monitor. MEDIUM = investigate within 1 hour. HIGH = kill switch, investigate before restart. CRITICAL = kill switch immediately, flatten all, full audit before restart."

**ACTION**: Replace 4-row escalation matrix with 4-line severity scale.

### GAP-13: Enforcement Points Table
**VERDICT: CUT — belongs in code, not plan**

P2 (Architect): A mapping of "rule → enforcement module" belongs in code comments or a CONTRIBUTING.md, not in the architectural plan. It's also already stale — the table references modules that don't enforce what's claimed (e.g., "feeds/data_validator.py" for R9-R12, but data_validator.py doesn't exist in the codebase).

P4 (Fund Manager): This table generates zero alpha and zero safety. Cut it.

### GAP-14: R5 Overnight Hold Clarification
**VERDICT: KEEP**

P3 (CRO): All leveraged ETPs closed by 16:25 UK during paper/limited live is correct. The code only enforces for 5x (virtual_trader.py line 1402). This gap is real and the fix is important. Keep.

### GAP-17: Daily Operations Log Template
**VERDICT: TRIM — keep concept, cut template**

P4 (Fund Manager): A daily ops log is good practice. But putting a JSON template in the architectural plan is the wrong place. The plan should say: "Daily structured log required in artifacts/daily_ops_log.json with: date, tickers scanned, signals generated/blocked/executed, daily P&L, system health status."

**ACTION**: Replace verbose template with 1-paragraph requirement.

---

## PART 2: EXISTING PLAN — CONTRADICTIONS TO FIX

### CONTRADICTION 1: "0.75% Hard Cap REMOVED" (§5.5) vs "Max risk = 0.75%, NO override" (§6 R-02)
**VERDICT: §6 R-02 governs. Remove the "cap removed" language from §5.5.**

P1 (Quant): 0.75% per-trade risk is sacred. At 55% WR, it takes 133 consecutive losers to reach ruin from 100% equity. Removing this cap is reckless.

P3 (CRO): The code enforces 0.75% (settings.yaml line 618, dynamic_sizer.py line 63 `_IMMUTABLE_MAX_RISK_PCT`). The plan should match the code. The "removal" was theoretical bloat from v12→v13 transition.

**ACTION**: Remove the paragraph in §5.5 that claims the cap was removed. R-02's 0.75% is immutable.

### CONTRADICTION 2: CDaR Lookback 60-day vs 252-day
**VERDICT: 60-day for current phase. 252-day is aspirational minimum for statistical power.**

P1 (Quant): At 413 trades, a 252-day lookback IS your entire dataset. Use 60-day rolling for responsive CDaR, note that 252-day is the statistical minimum for reliable estimation.

**ACTION**: Clarify in §5.3: "60-day rolling window for CDaR computation. Note: 252-day minimum recommended for statistical reliability (future phase)."

### CONTRADICTION 3: Correlation Brake (pairwise ρ > 0.70) vs Factor Exposure Cap (Nasdaq β > 1.5x)
**VERDICT: Factor exposure cap (GPT-45) replaces pairwise. Remove the dead pairwise rule.**

P1 (Quant): Pairwise correlation for 12 NASDAQ-correlated ETPs is always > 0.70. The rule is permanently triggered, so it's useless. Factor exposure (Nasdaq beta) is the correct approach.

**ACTION**: Remove pairwise correlation brake from §5.4 and §6 R-06. GPT-45 factor exposure cap is the binding rule.

### CONTRADICTION 4: HMM 3 states (sacred) vs 7/8 regime states (used everywhere)
**VERDICT: The 3-state HMM is the LATENT model. The 8 regime states are the OBSERVABLE trading states.**

P1 (Quant): This isn't actually a contradiction. Hamilton (1989) HMM uses 3 latent states. The regime classifier maps these + VIX + trend indicators into 8 actionable trading states. The sacred parameter "HMM States = 3" refers to the latent model, not the observable trading regime.

**ACTION**: Add a clarifying note to Table D/F: "HMM States = 3 refers to latent model (Hamilton 1989). The 8 observable trading regimes are derived from HMM output + rule-based overlays."

### CONTRADICTION 5: SessionProtection +1.5% vs +2.0% target
**VERDICT: Already fixed in settings.yaml (line 605: +2.0%). Plan references old value.**

Code reality: settings.yaml line 605 shows session protection stop at +2.0%. GPT-111 was correct that +1.5% blocks the target, but the code was already updated. Plan references need to be cleaned up.

**ACTION**: Update all plan references to SessionProtection to +2.0% (matching settings.yaml).

### CONTRADICTION 6: Drawdown cascade thresholds (R-04 vs settings.yaml)
**VERDICT: settings.yaml is source of truth. Align plan to code.**

Code reality (settings.yaml lines 769-795):
- YELLOW: -3% to -5%
- ORANGE: -5% to -8%
- RED: -8% to -10%
- CRITICAL: -10% to -12%
- EMERGENCY: <-12%

Plan (§6 R-04):
- GREEN: 0-2%
- YELLOW: 2-3%
- ORANGE: 3-4%
- RED: 4-5%
- HALT: >8%

These are COMPLETELY different. The code is much more generous (wider bands). The Constitution's L1(-1.5%)/L2(-2.5%)/L3(-4%) are INTRADAY triggers, separate from the accumulated recovery cascade.

**ACTION**: Align plan's accumulated drawdown cascade to match settings.yaml. The current plan thresholds are tighter than code and will cause confusion.

---

## PART 3: BLOAT TO CUT FROM THE ENTIRE PLAN

### CUT 1: Apex Scout / Section 3 (Lines ~3200-3800)
**STATUS**: 100% aspirational. Zero code. Zero data. apex_scout.py doesn't exist.

P4 (Fund Manager): Keep Section 3 as a 1-paragraph "future Phase C" bookmark. Cut the 600 lines of detailed specification for a module that doesn't exist.

**ACTION**: Replace Section 3 with: "Phase C: Universe expansion to 200-500 tickers via Scout module. Detailed specification deferred until Phase A complete."

### CUT 2: Per-Ticker Vol Regime Layer (GAP-06)
Already covered above. Remove entirely.

### CUT 3: G9 PDF Consistency Gate (GAP-10)
Already covered above. Remove.

### CUT 4: Enforcement Points Table (GAP-13)
Already covered above. Remove.

### CUT 5: Escalation Matrix (GAP-12)
Already covered above. Replace with 4-line severity scale.

### CUT 6: Verbose Daily Ops Log Template (GAP-17)
Already covered above. Replace with 1-paragraph requirement.

### CUT 7: 28-Entry Regime Transition Matrix Reference (GAP-07)
Already covered above. Keep 5 key transitions, remove archive reference.

---

## PART 4: WHAT TO STRENGTHEN

### STRENGTHEN 1: Unified Threshold Table
The plan has thresholds scattered across 20+ sections. settings.yaml has different values. Constitution has different values. Code has different values.

**ACTION**: Create a SINGLE "Source of Truth" threshold table in §10 that lists:
| Parameter | Plan Value | Code Value (file:line) | Constitution Value | BINDING |
This table is the authority. Any conflict, this table wins.

### STRENGTHEN 2: The 8 P0 Fixes That Still Aren't Fixed
27 stop-ship items identified across R12-R15. ZERO fixed in code. The plan keeps adding amendments describing fixes, but the code stays broken.

**ACTION**: Add a prominent "CURRENT STOP-SHIP STATUS" section at the TOP of the plan (after §0.5) with the 8 most critical P0 items and their fix status. This should be the FIRST thing anyone reads.

### STRENGTHEN 3: "No Emotion" Trading Rules
From predecessor systems and user's request:
- If there are 5 qualified trades, take them all simultaneously (multi-trade rule GPT-88)
- If there are 0 qualified trades, stay silent — DO NOT lower standards
- Never revenge trade, never chase, never hope
- These are already in trading_discipline.py (7 gates, 10 commandments) and settings.yaml (12 emotional firewall patterns)

**ACTION**: Ensure §6B (Trading Discipline Engine) clearly states the "no emotion" operating principles from the code, not aspirational additions.

### STRENGTHEN 4: The Actual Profit Ladder
Three implementations exist. Only VirtualTrader's inline ladder fires. The plan describes the dead ChandelierExit ladder. This is the single most important inconsistency in the entire system.

**ACTION**: §4.4 must document the ACTUAL firing ladder (VT inline), not the dead one. Mark ChandelierExit as "Phase B: consolidation target."

---

## PART 5: FINAL VERDICT SUMMARY

| Item | Action | Reason |
|------|--------|--------|
| GAP-01 (L1/L2/L3 reconciliation) | KEEP, rewrite for clarity | Genuine conflict resolved |
| GAP-02 (R19 partial exit) | KEEP | Essential for profit ladder |
| GAP-03 (drift 15%) | KEEP | One-line Constitutional alignment |
| GAP-04 (R4 40% cap) | KEEP, note non-binding at £10K | Future-proofing |
| GAP-05 (weekly/monthly breakers) | KEEP, trim language | Fills genuine gap |
| GAP-06 (per-ticker vol regime) | **CUT** | Aspirational complexity |
| GAP-07 (28-entry transition matrix) | TRIM to 5 key transitions | Operational focus |
| GAP-08 (failure drills) | KEEP, minor trim | Operationally essential |
| GAP-09 (G7 drawdown recovery) | KEEP | Simple, testable |
| GAP-10 (G9 PDF consistency) | **CUT** | Unimplementable |
| GAP-11 (rollback procedure) | KEEP, trim | Operationally essential |
| GAP-12 (escalation matrix) | **REPLACE** with 4-line scale | One-person operation |
| GAP-13 (enforcement points) | **CUT** | Belongs in code, not plan |
| GAP-14 (R5 overnight) | KEEP | Real gap |
| GAP-17 (daily ops log) | TRIM to 1-paragraph | Keep concept, cut template |
| §5.5 0.75% cap removal | **REMOVE** | Contradicts R-02, wrong |
| §5.3 CDaR lookback | CLARIFY | 60-day rolling, 252-day aspirational |
| §5.4 pairwise correlation | **REMOVE** | Dead rule, replaced by GPT-45 |
| Table D/F HMM 3 states | CLARIFY | Latent vs observable distinction |
| SessionProtection | UPDATE to +2.0% | Already fixed in code |
| Drawdown cascade | ALIGN to settings.yaml | Plan vs code mismatch |
| Section 3 Apex Scout | **COMPRESS** to 1-paragraph | 100% unimplemented |
| Profit ladder §4.4 | REWRITE to actual VT ladder | Currently describes dead code |

**Total items CUT or REPLACED**: 8
**Total items KEPT**: 10
**Total items STRENGTHENED**: 4
**Total contradictions resolved**: 6

---

## IMPLEMENTATION ORDER FOR PLAN EDITS

1. Remove per-ticker vol regime (GAP-06) from §6D
2. Remove G9 from Go-Live Gate
3. Remove enforcement points table (GAP-13)
4. Replace escalation matrix (GAP-12) with 4-line scale
5. Trim daily ops log template (GAP-17) to 1-paragraph
6. Trim regime transition matrix (GAP-07) to 5 transitions
7. Remove "0.75% cap removed" from §5.5
8. Remove pairwise correlation brake from §5.4
9. Clarify CDaR lookback in §5.3
10. Clarify HMM states in Table D/F
11. Update SessionProtection references to +2.0%
12. Align drawdown cascade to settings.yaml
13. Update version history to v13.15

---

*R17 Audit Complete. Zero tolerance for bloat. Every surviving item earns its place.*
