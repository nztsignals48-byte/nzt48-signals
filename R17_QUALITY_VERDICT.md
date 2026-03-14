# R17 QUALITY VERDICT — Kill-or-Keep for Every Predecessor Addition

**Auditor**: Claude Opus 4.6 (4-persona: Chief Quant, Lead Architect, CRO, Academic)
**Date**: 2026-03-06
**Principle**: Every addition must (1) make the system BETTER than before, (2) have a reason, (3) make sense. If it fails any test, it's cut.

---

## METHODOLOGY

For each predecessor addition, I asked:
1. **Does it work?** Is there code that enforces it, or is it aspirational?
2. **Is it better?** Does it improve risk-adjusted returns, reduce drawdown, or prevent ruin vs what was there before?
3. **Does it make sense?** At 1 trade/day with 12 ISA tickers and £10K equity, is this relevant?
4. **Is it a duplicate?** Does something already in the plan cover this?
5. **Reason for keeping**: One sentence justifying existence.

---

## SECTION 1: CONSTITUTIONAL RECONCILIATION (GAP-01 through GAP-05)

### GAP-01: L1/L2/L3 Intraday Circuit Breakers
| Test | Result |
|------|--------|
| **Works?** | YES — code (`circuit_breakers.py` lines 42-45) already has L1=1.5%, L2=2.5%, L3=4.0% matching Constitution |
| **Better?** | YES — distinguishes intraday breakers (L1/L2/L3) from accumulated drawdown cascade (GPT-67). Before GAP-01, the plan had TWO conflicting threshold systems with no reconciliation |
| **Makes sense?** | YES — at 3x leverage, a -1.5% move is a -4.5% underlying move. Early warning is critical |
| **Duplicate?** | No — it RECONCILES two pre-existing systems that were contradicting each other |
| **Reason**: Resolves the most dangerous contradiction in the plan — two competing drawdown systems. The reconciliation (intraday vs accumulated) is architecturally sound |
| **VERDICT**: **KEEP** |

### GAP-02: R19 Partial Exit Amendment
| Test | Result |
|------|--------|
| **Works?** | YES — the profit ladder already does partial exits. This is a constitutional amendment, not a code change |
| **Better?** | YES — partial exits with trailing optimize geometric mean. R19's "full exit on target" was written before the ladder existed and is geometrically suboptimal |
| **Makes sense?** | YES — the Kelly re-derivation (GPT-29, GPT-101) depends on the ladder's partial exit structure |
| **Duplicate?** | No |
| **Reason**: Without this amendment, the profit ladder violates the Constitution. With it, the system is consistent |
| **VERDICT**: **KEEP** |

### GAP-03: Parameter Drift Limit (±15%)
| Test | Result |
|------|--------|
| **Works?** | NO code exists. This is a future implementation target |
| **Better?** | YES — ±15% (Constitution) is tighter than ±20% (GPT-77). Tighter bounds prevent catastrophic ML drift |
| **Makes sense?** | YES — any adaptive system without constitutional bounds can self-destruct (López de Prado 2018) |
| **Duplicate?** | No — corrects an internal contradiction (R21 said 20%, R23 said 15%) |
| **Reason**: Aligns the plan to the Constitution's binding authority. Without this, R21 and R23 contradict each other |
| **VERDICT**: **KEEP** (text corrected to ±15% in R17) |

### GAP-04: R4 Total Deployment Cap (40%)
| Test | Result |
|------|--------|
| **Works?** | NO code exists |
| **Better?** | YES — prevents the system from deploying 80%+ of equity in leveraged products. The 6% portfolio heat cap (risk-based) allows high notional deployment in low-volatility tickers |
| **Makes sense?** | YES at 3x leverage — 40% notional = 120% effective market exposure. Without this, 3 positions at 10% each = 30% notional = 90% exposure, which is tolerable but near the limit |
| **Duplicate?** | Partially overlaps with R3 (10% per position) and portfolio heat cap, but neither limits AGGREGATE notional |
| **Reason**: The only aggregate exposure cap in the system. R3 caps per-position, heat caps risk, but nothing caps total notional. One missing and you can be 100% deployed in correlated leveraged ETPs |
| **VERDICT**: **KEEP** — but needs implementation in DynamicSizer |

### GAP-05: Weekly -8% / Monthly -15% Constitutional Breakers
| Test | Result |
|------|--------|
| **Works?** | PARTIAL — settings.yaml has weekly -6% (`max_weekly_loss: 0.06`). No monthly breaker |
| **Better?** | YES — adds a missing time dimension to risk management. Daily breakers can reset; weekly/monthly cannot |
| **Makes sense?** | YES — a system that loses -3.9% daily (just under L3) for 3 consecutive days loses -11.7% without triggering any weekly halt. Weekly -8% catches this |
| **Duplicate?** | Extends existing -6% weekly from settings.yaml. Constitution's -8% is the hard stop; -6% is the early warning |
| **Reason**: Without multi-day cumulative breakers, the system can bleed out slowly under daily limits |
| **VERDICT**: **KEEP** |

---

## SECTION 2: REGIME INTEGRITY CONTROLS (GPT-79 through GPT-82, GPT-89)

### Regime Flapping Protection (GPT-80)
| Test | Result |
|------|--------|
| **Works?** | NO code exists. Plan-only |
| **Better?** | YES — without this, the regime classifier oscillating at a VIX threshold boundary causes rapid long/short/long/short signals in the same session |
| **Makes sense?** | YES — 3+ regime changes in 10 minutes is almost certainly a data issue or threshold boundary problem, not real market regime shifts |
| **Duplicate?** | Complements VIX hysteresis (GPT-46) which handles threshold boundaries. Flapping catches any cause of rapid oscillation |
| **Reason**: Prevents the system from trading against itself during classifier instability |
| **VERDICT**: **KEEP** — straightforward to build (track transition timestamps, count within window) |

### Post-Recovery Ramp-Up (GPT-81)
| Test | Result |
|------|--------|
| **Works?** | PARTIAL — DynamicSizer has SHOCK_RECOVERY (3 sessions at 0.25x). No RISK_OFF recovery |
| **Better?** | YES — the first "normal" after SHOCK is often a dead cat bounce. Jumping to full size immediately is reckless |
| **Makes sense?** | YES — empirically, post-crash bounces have higher volatility and lower signal quality. The 0.25x buffer is conservative |
| **Duplicate?** | Extends existing SHOCK_RECOVERY to cover RISK_OFF and EMERGENCY_FLATTEN transitions |
| **Reason**: Prevents full-size entries during false recoveries |
| **VERDICT**: **KEEP** |

### Regime Stuck Detection (GPT-82)
| Test | Result |
|------|--------|
| **Works?** | NO code exists |
| **Better?** | YES — a classifier that silently returns the same regime for 24+ hours during a 5% market move has failed. This alert catches silent classifier death |
| **Makes sense?** | YES — simple comparison (regime_start vs now). Near-zero implementation cost |
| **Duplicate?** | No |
| **Reason**: Detects silent classifier failure. Cost: 3 lines of code. Benefit: prevents trading on stale regime classification |
| **VERDICT**: **KEEP** |

### Drought-Regime Contradiction Detection (GPT-79)
| Test | Result |
|------|--------|
| **Works?** | NO code exists |
| **Better?** | YES — catches internal inconsistencies (e.g., TRENDING market + zero signals = something is broken) |
| **Makes sense?** | YES — 5 simple boolean rules. Acts as a smoke detector for systemic issues |
| **Duplicate?** | No |
| **Reason**: Self-consistency checks are the cheapest and most valuable diagnostic tool. A TRENDING market with zero signals for 20 cycles means gates are miscalibrated |
| **VERDICT**: **KEEP** |

### Drought State Machine (GPT-89)
| Test | Result |
|------|--------|
| **Works?** | NO code exists |
| **Better?** | YES — replaces ad-hoc "no signals" counters with a formal state machine (NONE→WATCH→ACTIVE→CRITICAL) |
| **Makes sense?** | YES — at 1 trade/day, 10+ dry days is a realistic scenario in range-bound markets. Quality threshold decay at CRITICAL (65→50 over days) prevents the system from sitting idle for months |
| **Duplicate?** | No |
| **Reason**: Handles the most common real-world scenario (extended periods with no qualifying trades) with graduated responses instead of binary "trade or don't" |
| **VERDICT**: **KEEP** — but the floor at 50 is critical. The "do NOT lower standards just to trade" message at 5 consecutive no-trade days is the most important behavioral guardrail |

---

## SECTION 3: OPERATIONAL INFRASTRUCTURE (GPT-78, GPT-84, GPT-85, GPT-86, GPT-90)

### Startup Readiness Gate (GPT-78)
| Test | Result |
|------|--------|
| **Works?** | NO dedicated module, but the concept is partially enforced in main.py startup sequence |
| **Better?** | YES — without this, the engine can start trading with stale data, missing Redis state, or broken API connections |
| **Makes sense?** | YES — 8 pre-flight checks (data feed, Redis, broker API, disk space, time sync, regime classifier, position reconciliation, data coverage) are basic operational hygiene |
| **Duplicate?** | No |
| **Reason**: Prevents the engine from trading in a degraded state after a restart |
| **VERDICT**: **KEEP** |

### Evidence Preservation Protocol (GPT-84)
| Test | Result |
|------|--------|
| **Works?** | NO code exists |
| **Better?** | YES — "preserve before fix" is the #1 lesson from operational incidents. A restart destroys in-memory state |
| **Makes sense?** | YES — 5 commands (snapshot Redis, logs, health, positions → then fix). Near-zero cost, prevents post-mortem blindness |
| **Duplicate?** | No |
| **Reason**: Without evidence, you can't diagnose what went wrong. Restarts are the default instinct but they destroy the crime scene |
| **VERDICT**: **KEEP** |

### Daily Operational Checklists (GPT-85)
| Test | Result |
|------|--------|
| **Works?** | NO automation, but provides structure for manual pre-market/post-market review |
| **Better?** | YES — transforms ad-hoc monitoring into systematic review |
| **Makes sense?** | YES — trimmed in v13.15 to essential fields only. Not bloated |
| **Duplicate?** | No |
| **Reason**: A solo operator without checklists will eventually miss something critical |
| **VERDICT**: **KEEP** (trimmed version) |

### LIMITED LIVE Transition Plan (GPT-86)
| Test | Result |
|------|--------|
| **Works?** | NO code exists — this is a future operational procedure |
| **Better?** | YES — £1K allocation, 1 position max, human confirmation required. Gradual deployment prevents catastrophic failure on first live trade |
| **Makes sense?** | YES — going from paper to live at full size is reckless. 10 MTRL days at £1K is the minimum responsible transition |
| **Duplicate?** | No |
| **Reason**: The gap between paper and live is where most systems fail. This bridge phase catches execution issues (slippage, fill quality) at minimal risk |
| **VERDICT**: **KEEP** |

### Circuit Breaker Persistence (GPT-90)
| Test | Result |
|------|--------|
| **Works?** | NO — `circuit_breakers.py` stores all state in memory. Docker restart = state loss |
| **Better?** | YES — if the engine restarts after an L3 halt, the halt flag is lost. The system resumes trading despite being in crisis |
| **Makes sense?** | YES — serialize to Redis or JSON on every state change. 20 lines of code |
| **Duplicate?** | No |
| **Reason**: Without persistence, a Docker restart bypasses every circuit breaker. This is a P0 safety bug |
| **VERDICT**: **KEEP** — MUST BUILD before any live trading |

---

## SECTION 4: TRADING DISCIPLINE ENGINE (GPT-75, §6B)

### 10 Commandments + 7 Discipline Gates
| Test | Result |
|------|--------|
| **Works?** | YES — `core/trading_discipline.py` (437 lines) is FULLY WIRED. R14 verified: 3 entry points, all 7 gates, all thresholds match |
| **Better?** | YES — the emotional/behavioral framework was in the CODE but never referenced by the plan. The plan was incomplete without it |
| **Makes sense?** | YES — rules like "never widen stops", "mandatory stop with entry", "cooldown after 4 consecutive losses" are the behavioral foundation of systematic trading |
| **Duplicate?** | No — these rules complement the quantitative risk controls with behavioral guardrails |
| **Reason**: The most powerful protection against the operator's own worst instincts. Code-enforced emotional discipline |
| **VERDICT**: **KEEP** — best example of predecessor wisdom that was already working in code |

---

## SECTION 5: ITEMS CORRECTLY CUT (v13.15)

| Item | Why It Was Cut | Was the Cut Correct? |
|------|---------------|---------------------|
| Per-ticker vol regime (GAP-06) | Zero code, zero data, insufficient trades to calibrate 5 states × 12 tickers = 60 categories | **YES** — at 1 trade/day, you'd need 3,000 trades (12 years!) to calibrate |
| G9 PDF Consistency gate | No automated way to detect "contradictions" between PDF narrative and data | **YES** — this is human review, not a gate |
| Enforcement Points Table (GAP-13) | Rule-to-module mapping belongs in code, not architecture plan. Table referenced non-existent files | **YES** — already stale on arrival |
| 28-entry regime transition matrix → 5 key transitions | 23 of 28 transitions are irrelevant at 1 trade/day | **YES** — only catastrophic transitions matter: Any→SHOCK, Any→RISK_OFF, TRENDING_UP→DOWN, recovery ramps |
| Bloated ops log template → 1-line field spec | Over-engineered for solo operator | **YES** |
| Escalation matrix → 4-line severity scale | Same — over-engineered for solo operator | **YES** |

---

## SECTION 6: ITEMS CORRECTLY DEFERRED

| Item | Why Deferred | Correct? |
|------|-------------|----------|
| GAP-15: Weekly IC Review Memo (R25) | For solo operator, the weekly report (GPT-98) already covers this | **YES** — duplicate |
| GAP-16: Startup Session Window Integration | The startup gate (GPT-78) already runs on startup. Specifying exact re-check times is Phase B polish | **YES** |
| GAP-18: Session Classification Taxonomy | US-hours only in code. At 1 trade/day, session classification is a refinement | **YES** — Phase C |
| GAP-19: Intraday Momentum Bias | First-hour-to-last-hour scalar is HFT refinement | **YES** — Phase C |
| GAP-20: Startup Acceptance Tests | GPT-78's 8 checks ARE the acceptance tests. Separate test naming is bureaucratic | **YES** — covered by GPT-78 |

---

## SECTION 7: NO-EMOTION TRADING RULES (from predecessor systems)

These rules were in `settings.yaml` (12 emotional firewall patterns) and `core/trading_discipline.py` (10 Commandments). They are the most valuable predecessor wisdom and were correctly added to the plan:

| Rule | Source | In Plan? | In Code? | Better Than Before? |
|------|--------|----------|----------|-------------------|
| Never widen stops (R18) | Constitution | YES | YES (discipline gate) | YES — prevents the #1 retail trader mistake |
| Mandatory stop with entry (R17) | Constitution | YES | YES (VirtualTrader enforces) | YES — no naked positions |
| Never average down | 10 Commandments | YES (§6B) | YES (discipline gate) | YES — averaging into a loser is ruin |
| Cooldown after consecutive losses | 7 Gates | YES (§6B) | YES (Gate 2: 120min after 4 losses) | YES — prevents tilt/revenge trading |
| If no qualified trades, stay silent | Drought SM | YES (GPT-89) | NO code yet | YES — "the market owes us nothing" is the most important sentence in the plan |
| If 5 top trades qualify simultaneously, take them all | Multi-Trade Rules (GPT-88) | YES (§6B) | PARTIAL (up to regime position limit) | YES — but capped by R1 position limits and R4 deployment cap |
| Never force trades in drought | Drought SM floor | YES (quality floor = 50) | NO code yet | YES — quality floor prevents desperation trading |

**VERDICT ON NO-EMOTION RULES**: All **KEEP**. These are the behavioral foundation. The plan was dangerously incomplete without them.

---

## FINAL SCORECARD

| Category | Added | Kept | Cut | Deferred | Net Better? |
|----------|-------|------|-----|----------|-------------|
| Constitutional Reconciliation (GAP-01–05) | 5 | 5 | 0 | 0 | YES — resolves 5 contradictions |
| Regime Integrity (GPT-79–82, 89) | 5 | 5 | 0 | 0 | YES — catches silent failures |
| Operational Infrastructure (GPT-78,84,85,86,90) | 5 | 5 | 0 | 0 | YES — basic operational hygiene |
| Trading Discipline (GPT-75, §6B) | 1 | 1 | 0 | 0 | YES — behavioral guardrails |
| v13.15 Cuts | 6 | 0 | 6 | 0 | YES — removed bloat |
| Deferred Items | 5 | 0 | 0 | 5 | YES — honest deferral |
| **TOTAL** | **27** | **16** | **6** | **5** | **ALL NET POSITIVE** |

**Zero additions make the system worse.** Every surviving item either:
- Resolves a contradiction that could cause catastrophic failure (GAP-01–05)
- Catches a silent failure mode (GPT-79–82, 89)
- Provides basic operational safety (GPT-78, 84, 85, 86, 90)
- Enforces trading discipline (GPT-75)

Every cut item was either aspirational bloat (per-ticker vol regime), unimplementable (G9 PDF gate), or a duplicate (IC review memo).

---

## CONTRADICTIONS FIXED IN R17

9 contradictions were found and fixed in the master plan during this audit:

| # | Contradiction | Fix Applied |
|---|--------------|-------------|
| 1 | Line 4908: "0.75% cap is redundant and harmful" vs line 4904: "cap is IMMUTABLE" | Deleted line 4908. Cap is IMMUTABLE per code and Constitution |
| 2 | Line 6866: "Kelly Sizing Cap: REMOVED" | Changed to "0.75% IMMUTABLE" with code reference |
| 3 | R21 body text says ±20% (line 5051) but GAP-03 says ±15% | Fixed R21 to ±15%. Constitution governs |
| 4 | GPT-109 says normalize DD to 3% but GAP-01/Constitution says L3=4% | Rewrote GPT-109 — the 4 systems form a cascade (discipline -3% soft → Constitution L2 -2.5% hard → L3 -4% hard) |
| 5 | Lines 7702, 7908: "7 latent states" vs line 6930: "3 latent states" | Fixed to "3 latent + 8 observable" everywhere |
| 6 | Line 4644: "REQUIRED: Use Cornish-Fisher" vs GPT-43: "Historical Simulation VaR" | Fixed to Historical Simulation VaR. CF retained as cross-check only |
| 7 | Line 6637: Phase A item still says "CDaR Cornish-Fisher" | Updated to "CDaR Historical Simulation VaR" |
| 8 | Line 8004: References G9 PDF Consistency (cut in v13.15) | Updated to note G9 was cut |
| 9 | Line 6817: "23 stop-ship items" vs lines 6765, 7995: "27" | Fixed to 27 everywhere |

---

## OPEN ITEMS (NOT IN PLAN — ACKNOWLEDGED AS GAPS)

These items exist in predecessor systems but are NOT in the plan. They have been deliberately deferred with documented reasons, not silently dropped:

| Item | Why Deferred | Phase |
|------|-------------|-------|
| Weekly IC Review Memo (R25) | Duplicate of weekly report | N/A (covered) |
| Session Classification Taxonomy | US-hours only, needs LSE adaptation | Phase C |
| Intraday Momentum Bias | HFT refinement, irrelevant at 1 trade/day | Phase C |
| Startup Session Windows (specific re-check times) | Polish, not critical path | Phase B |
| Formal Acceptance Test IDs (T-STARTUP-001 etc) | GPT-78's 8 checks serve this purpose | N/A (covered) |
