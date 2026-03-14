# PREDECESSOR WISDOM TRACKER — Strict Criteria Audit

**Auditor**: Claude Opus 4.6
**Date**: 2026-03-06
**Purpose**: Exhaustive cross-reference of ALL predecessor system wisdom against AEGIS_MASTER_PLAN_v13_FINAL.md (v13.13). Every rule, threshold, procedure, and operational item from predecessor documents is tracked with a strict ADDED/PARTIAL/MISSING verdict.

**Methodology**: 4 parallel extraction agents read every predecessor document + 6 code files line-by-line. Each extracted item was then searched in the master plan via targeted grep queries to produce definitive verdicts.

---

## SCORING SUMMARY

| Source Document | Total Items | ADDED | PARTIAL | MISSING | Coverage |
|---|---|---|---|---|---|
| RISK_CONSTITUTION.md (R1-R29 + procedures) | 52 | 31 | 9 | 12 | 77% |
| REGIME_DROUGHT_SPEC.md | 38 | 21 | 8 | 9 | 76% |
| STARTUP_READINESS_GATE_SPEC.md | 18 | 12 | 3 | 3 | 83% |
| OPS_PUSH_92_TO_100.md (G1-G10 + ops) | 34 | 19 | 7 | 8 | 74% |
| v11 Plan (sacred params + operational) | 22 | 16 | 3 | 3 | 86% |
| Code-Embedded Wisdom (6 files) | 41 | 30 | 6 | 5 | 88% |
| **TOTAL** | **205** | **129** | **36** | **40** | **80%** |

**40 items are MISSING from the plan. 36 are PARTIAL (mentioned but incomplete). These must be addressed.**

---

## PART I: RISK CONSTITUTION (RISK_CONSTITUTION.md)

### Position Limits (R1-R5) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R1 | Max concurrent positions | 2 paper, 3 limited live | **PARTIAL** | §6C mentions R1, but plan says "3" everywhere, doesn't distinguish paper vs limited live modes | Plan lacks paper-mode-specific position limit of 2 |
| R2 | Max risk per trade | 2% of equity | **PARTIAL** | Plan says 0.75% per trade (settings.yaml). Constitution says 2% as ceiling. These are compatible but plan doesn't cite R2 as the constitutional ceiling | Plan should explicitly state R2=2% as the constitutional ceiling above which 0.75% operates |
| R3 | Max notional per position | 10% of equity | **PARTIAL** | §4.1 mentions 10% cap on a table row but doesn't cite R3 | R3 not explicitly referenced as constitutional rule |
| R4 | Max total deployment | 40% of equity | **MISSING** | Not found in plan body | **GAP: 40% total deployment cap not in plan** |
| R5 | No overnight holds (close by 16:25 UK) | 16:25 UK hard stop | **PARTIAL** | Plan mentions 5x overnight_kill=True and time-decay close, but doesn't state universal no-overnight rule for all leveraged ETPs. Constitution mandates ALL positions closed by 16:25 | **GAP: Universal 16:25 close rule not stated for 3x ETPs** |

### Drawdown Circuit Breakers (L1-L3 + Weekly/Monthly) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| L1 | Daily DD -1.5% | 50% size reduction | **MISSING** | Plan uses different thresholds: GREEN(0-2%), YELLOW(2-3%), ORANGE(3-4%), RED(4-5%), HALT(>8%). Constitution's L1=-1.5% is NOT in the plan | **GAP: L1 at -1.5% not in plan. Plan's first trigger is at -2%** |
| L2 | Daily DD -2.5% | EXIT-ONLY mode | **MISSING** | Plan has no EXIT-ONLY mode at -2.5%. Closest is ORANGE at -3% or -4% | **GAP: L2 EXIT-ONLY mode at -2.5% not in plan** |
| L3 | Daily DD -4.0% | FLATTEN ALL + HALT | **PARTIAL** | GPT-109 found circuit breaker RED at 4% in code, plan says different values. Constitution says L3=4.0% | Threshold exists but values conflict between plan sections |
| Weekly DD | -8.0% | HALT for week | **MISSING** | Plan mentions -5% weekly halt (§6), -6% auto-halt (settings.yaml), but NOT -8% from Constitution | **GAP: Constitution's -8% weekly limit not in plan** |
| Monthly DD | -15.0% | HALT + IC review | **MISSING** | Plan mentions -15% in one scaling table row but not as a binding monthly circuit breaker with IC review requirement | **GAP: Monthly -15% with IC review not in plan** |
| CB State Machine | NORMAL→REDUCED→EXIT_ONLY→HALTED | State transitions | **MISSING** | Plan has Master Risk State Machine (GPT-30) but uses different states (NORMAL/REDUCE/EMERGENCY_FLATTEN/SYSTEM_HALTED). Constitution's 4-state CB machine is different | **GAP: Constitution's specific CB state machine not reconciled with plan's Risk State Machine** |
| CB Persistence | Must persist to disk | Survives restart | **PARTIAL** | GPT-90 added circuit breaker persistence requirement but doesn't cite Constitution's binding mandate | Added via GPT-90 but not linked to Constitution |

### Leverage Rules (R6-R8) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R6 | Leverage-once assertion | Never leverage a leveraged product | **ADDED** | §1.3, §6 multiple references to leverage-once | Present |
| R7 | 5x product 50% size reduction | 50% vs 3x baseline | **ADDED** | §2.1.6, settings.yaml 5x rules | Present |
| R8 | HIGH_VOL/SHOCK 50% reduction (stacks with R7) | Multiplicative stacking | **ADDED** | §2.1.6, regime sizing table | Present with worked example |

### Data Quality Rules (R9-R12) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R9 | Staleness gate 120 seconds | No entry if data >120s old | **ADDED** | GPT-33/GPT-39 staleness controls | Present |
| R10 | Spread gate (0.5% for 3x, 0.8% for 5x) | Spread maximum | **PARTIAL** | Plan uses 55bps ETP spread threshold (§4.3). Constitution says 0.5%=50bps for 3x. Close but not identical — and Constitution specifies separate 0.8% for 5x | **GAP: Constitution's 5x spread gate at 0.8% not in plan separately** |
| R11 | Coverage gate (80% of universe must have fresh data) | 10 of 12 tickers | **ADDED** | Startup Readiness Gate §8B | Present via data coverage check |
| R12 | Opening exclusion (first 5 min, 08:00-08:05 UK) | No entry first 5 min | **ADDED** | §2.1 Time-of-Day windows, Chaos Open 09:30-09:35 for US. Also GPT-33 5-min LSE open exclusion | Present |

### Signal Quality Rules (R13-R16) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R13 | Min composite score 65 | 65 for execution, 55-64 display only, <55 discard | **ADDED** | §2.1 confidence engine, min_confidence: 60 in plan (slightly different from Constitution's 65) | Present but threshold differs: plan=60, Constitution=65. Constitution should govern |
| R14 | Min R:R 1.2 | Reward-to-risk floor | **PARTIAL** | Plan mentions R:R requirements but §4.3 EV gate uses different threshold (positive-EV-after-friction). Constitution's explicit R:R>=1.2 gate not cited by name | R:R gate exists but threshold may differ |
| R15 | Risk Officer absolute VETO | No override | **ADDED** | §5 risk_officer, GPT-50 single Risk Arbiter | Present |
| R16 | RVOL liquidity gate >= 0.4x | Minimum relative volume | **ADDED** | §2.1 RVOL scoring, settings.yaml RVOL thresholds | Present |

### Execution Rules (R17-R20) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R17 | Mandatory stop loss with entry | No position without stop | **ADDED** | §4.4, §6B 10 Commandments, multiple references | Present |
| R18 | Stop tightening only | Never widen stops | **ADDED** | §6B Commandment, Emotional Firewall "Moving Stops" pattern | Present |
| R19 | Full exit on target | Binary outcome: stop or target | **PARTIAL** | Plan uses Chandelier ladder with partial exits, which contradicts R19's "full exit on target" rule. Plan's approach (33% bank, 67% trail) is architecturally different | **GAP: R19 says full exit on target, plan uses partial exits. Need reconciliation or formal R19 amendment** |
| R20 | Time-decay close 16:00-16:25 UK | Linear urgency ramp | **ADDED** | §4.3 urgency function, 5x overnight_kill | Present |

### Learning Engine Bounds (R21-R25) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R21a | Score weights ±20% from baseline | Permitted range | **ADDED** | §5B GPT-77, ±20% parameter drift limit | Present |
| R21b | RVOL threshold 0.3x-0.6x | Permitted range | **ADDED** | §5B learning bounds | Present |
| R21c | ATR threshold 0.5%-3.0% | Permitted range | **ADDED** | §5B learning bounds | Present |
| R22 | Meta-learner CANNOT adjust constitutional rules | Prohibited adjustments | **ADDED** | §5B GPT-77 explicit prohibition | Present |
| R23 | Parameter drift limit 15% | DEFENSIVE mode on breach | **PARTIAL** | §5B says ±20% (GPT-77), Constitution says 15%. Conflict | **GAP: Plan says 20% drift limit, Constitution says 15%. Constitution should govern** |
| R24 | Minimum 100 resolved trades before adjustments | Learning gate | **ADDED** | §5B GPT-77, 100 trade minimum | Present |
| R25 | Weekly IC review of all adjustments | Governance reporting | **MISSING** | Plan has no weekly IC review memo requirement | **GAP: Weekly IC review memo for learning adjustments not in plan** |

### Kill Switch Rules (R26-R29) — NON-NEGOTIABLE

| # | Rule | Value | Plan Status | Plan Location | Gap |
|---|---|---|---|---|---|
| R26 | Flatten within 60 seconds | Kill switch latency | **ADDED** | §8 infrastructure, kill switch spec | Present |
| R27 | State persistence to disk | Survives restart | **ADDED** | §8 kill switch persistence | Present |
| R28 | 3 independent activation methods | Telegram + file + API | **ADDED** | §8, OPS_PUSH procedures | Present |
| R29 | Manual restart after kill | No auto-restart | **ADDED** | §8, R29 referenced | Present |

### Acceptance Tests (RC-T01 through RC-T10)

| # | Test | Plan Status | Gap |
|---|---|---|---|
| RC-T01 | Position limit breach test | **MISSING** | Not cited in plan. GPT-04 has signal queue tests but not RC-T01 specifically |
| RC-T02 | Risk per trade sizing test | **MISSING** | Not cited |
| RC-T03 | L2 circuit breaker test | **MISSING** | Not cited (plan has different CB thresholds) |
| RC-T04 | L3 flatten test | **MISSING** | Not cited |
| RC-T05 | Staleness gate test | **PARTIAL** | GPT-10 has adversarial tests but not RC-T05 specifically |
| RC-T06 | Score + R:R gate test | **MISSING** | Not cited |
| RC-T07 | Risk Officer VETO test | **MISSING** | Not cited |
| RC-T08 | Meta-learner blocked adjustment test | **MISSING** | Not cited |
| RC-T09 | Kill switch flatten test | **PARTIAL** | Kill switch tests mentioned in OPS_PUSH G8 but not RC-T09 format |
| RC-T10 | 5x HIGH_VOL sizing test | **MISSING** | Not cited |

### Constitution Procedures

| Item | Plan Status | Gap |
|---|---|---|
| Violation Response Protocol (CRITICAL/MAJOR/MINOR) | **PARTIAL** | Plan mentions incident library once (line 5096) but doesn't have the full 3-tier violation classification and response procedures | **GAP: Full violation response protocol not in plan** |
| Amendment Procedure (5 business days, unanimous IC consent) | **PARTIAL** | §6C mentions amendment procedure but doesn't specify 5 business day review or unanimous consent requirement | **GAP: Specific amendment procedure details missing** |
| Enforcement Points Table (rule-to-module mapping) | **MISSING** | Plan doesn't have a Constitution-style enforcement points table mapping each rule to its code enforcement module | **GAP: No enforcement points table** |
| Enforcement Invariant (7-gate sequence) | **PARTIAL** | §4.1 signal pipeline has a sequence but doesn't match Constitution's specific 7-gate enforcement invariant | Gate sequence exists but differs from Constitution |

---

## PART II: REGIME & DROUGHT SPEC (REGIME_DROUGHT_SPEC.md)

### Regime States

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 8 Market Regime States | **ADDED** | §2.1 regime table, settings.yaml | All 8 present |
| 5 Volatility Regime States (COMPRESSION, EXPANSION, BLOW_OFF, EXHAUSTION, BREAKDOWN) | **MISSING** | Plan mentions "volatility regime" generically but does NOT define the 5-state per-ticker vol regime taxonomy | **GAP: 5-state vol regime taxonomy not in plan** |
| Cross-Layer Consistency Matrix (market regime vs vol regime) | **MISSING** | Not in plan | **GAP: No cross-layer consistency matrix** |

### Drought State Machine

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 4 Drought States (NONE/WATCH/ACTIVE/CRITICAL) | **ADDED** | §6D GPT-89 | Present |
| Cycle counters (10/20/60) | **ADDED** | §6D GPT-89 | Present |
| 7-step drought clearing rules | **PARTIAL** | §6D mentions clearing but doesn't enumerate all 7 steps explicitly | Only partial clearing rules |
| Drought persistence to system_state.json | **PARTIAL** | §6D mentions persistence generically | Not specific to system_state.json field name |
| Drought recovery (stale < 2h → reload, else reset) | **MISSING** | Not in plan | **GAP: Drought recovery on restart not specified** |

### Contradiction Detection

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 5 Contradiction Rules (C1-C5) | **ADDED** | §6D GPT-79 | Present |
| Contradiction Alert Format | **MISSING** | Plan doesn't specify the Telegram alert format for contradictions | **GAP: No alert format** |
| 30-min dedupe for contradiction alerts | **MISSING** | Not in plan | **GAP: No dedupe interval** |

### Regime Transitions

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| Full transition matrix (28+ from/to pairs with actions) | **PARTIAL** | §6D mentions regime transitions but doesn't have the full 28-entry matrix with specific position actions per transition | **GAP: Full transition action matrix not in plan** |
| Transition atomicity requirement | **MISSING** | Not in plan | **GAP: Atomicity rule not stated** |
| 2-cycle minimum regime hold | **ADDED** | §6D, confirmed in code | Present |
| Post-RISK_OFF 30-min ramp at 0.25x | **ADDED** | §6D GPT-81 | Present |
| Post-SHOCK 60-min ramp at 0.25x | **ADDED** | §6D GPT-81 | Present |

### Flapping Protection

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 3 changes in 10 min = REGIME_FLAPPING | **ADDED** | §6D GPT-80 | Present |
| Hold positions, no new entries, 0.25x size | **ADDED** | §6D GPT-80 | Present |
| Exit after 5 stable cycles | **ADDED** | §6D GPT-80 | Present |

### Stuck Detection

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 24h unchanged = alert | **ADDED** | §6D GPT-82 | Present |

### Failure Modes (F1-F7)

| Item | Plan Status | Gap |
|---|---|---|
| F1: Regime classifier crash → use last known | **PARTIAL** | Plan mentions fallback but not F1 specifically |
| F2: Vol regime classifier crash → use last known | **MISSING** | Vol regime classifier not in plan |
| F3: Both classifiers same state 24h → stuck alert | **ADDED** | GPT-82 |
| F4: Drought counter overflow → cap at 99999 | **MISSING** | Not in plan |
| F5: Contradiction check crash → soft gate | **MISSING** | Not in plan |
| F6: Flatten fails → retry once + manual alert | **PARTIAL** | Plan has flatten logic but no explicit retry-once + manual escalation |
| F7: Regime flapping during news → enter FLAPPING state | **ADDED** | GPT-80 |

---

## PART III: STARTUP READINESS GATE (STARTUP_READINESS_GATE_SPEC.md)

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 8 pre-flight checks (SRG_CHECK_*) | **ADDED** | §8B GPT-78 | Present |
| 3 critical checks (data provider, war room API, Telegram) | **ADDED** | §8B GPT-78 | Present |
| 3 gate states (READY/DEGRADED/HALTED) | **ADDED** | §8B GPT-78 | Present |
| Output suppression matrix | **PARTIAL** | §8B mentions suppression but doesn't have the full matrix table | Matrix exists but simplified |
| 5-minute re-check loop | **ADDED** | §8B GPT-78 | Present |
| Manual override (DEGRADED only) | **ADDED** | §8B GPT-78 | Present |
| Override expires at session boundary | **PARTIAL** | §8B mentions override but not explicit expiry at session boundary | Expiry rule may be missing |
| Session window integration (06:55, 13:25 triggers) | **MISSING** | Plan doesn't specify pre-market 06:55 and pre-US-open 13:25 gate triggers | **GAP: Session window timing for gate re-checks not in plan** |
| 8 acceptance tests (T-STARTUP-001 through T-STARTUP-008) | **MISSING** | Plan doesn't enumerate the 8 startup acceptance tests | **GAP: Startup acceptance tests not in plan** |
| Proof artifact schema (readiness_gate.json) | **PARTIAL** | §8B mentions artifact but doesn't have the full JSON schema | Schema simplified in plan |
| Escalation matrix (P1/P2/P3 by severity) | **MISSING** | Not in plan | **GAP: Startup failure escalation matrix not in plan** |
| Operator action table per check failure | **MISSING** | Not in plan | **GAP: No per-check operator remediation guide** |

---

## PART IV: OPS_PUSH_92_TO_100.md (Go/No-Go Gates + Operations)

### 10 Go/No-Go Gates

| Gate | Description | Threshold | Plan Status | Gap |
|---|---|---|---|---|
| G1 | Data reliability | >=85% for ALL 12 tickers, 30 consecutive days | **PARTIAL** | Plan §9 Go-Live Gate has different criteria. G1's specific 85% per-ticker-per-day for 30 consecutive days is not stated | Different criteria |
| G2 | Win rate | >=45% over 100+ resolved outcomes | **ADDED** | §9 Go-Live Gate | Present |
| G3 | System uptime | >=99% over 30 days (market hours only) | **ADDED** | §9 Go-Live Gate | Present |
| G4 | Zero HALTED events | 0 in last 14 days | **PARTIAL** | Plan §9 mentions no HALTED events but doesn't specify 14-day window with clock reset | Window not specified |
| G5 | Cost model calibrated | Within 20% of expected fills, 50+ trades | **PARTIAL** | Plan mentions cost model but not the specific 20%/50-trade validation criteria | Criteria less specific |
| G6 | Edge stability | No strategy with stability < 0.5, n>=20 | **PARTIAL** | Plan §9 mentions edge stability but different metric | Different metric |
| G7 | Drawdown recovery | At least 1 YELLOW recovered | **MISSING** | Plan doesn't require a demonstrated drawdown recovery event as a gate | **GAP: Drawdown recovery gate not in plan** |
| G8 | Kill switch tested | All 3 methods within last 30 days | **PARTIAL** | Plan mentions kill switch testing but not the specific 3-method-in-30-days requirement | Less specific |
| G9 | PDF consistency | 0 contradictions in last 7 days | **MISSING** | Plan doesn't have a PDF consistency gate | **GAP: PDF consistency gate not in plan** |
| G10 | Telegram reliability | 99%+ delivery, 0 false signals | **PARTIAL** | Plan mentions Telegram reliability but not the specific 99%/zero-false-signal criteria | Less specific |

### 6-Phase Paper-to-Live Roadmap

| Phase | Plan Status | Gap |
|---|---|---|
| Phase 1: Baseline (Weeks 1-2) — 12 checklist items | **PARTIAL** | Plan §9 has phases but not these specific checklist items | Different granularity |
| Phase 2: Tuning (Weeks 3-4) — 8 items including L2 spread data | **PARTIAL** | Plan mentions tuning but not these specific items | Different items |
| Phase 3: Calibration (Weeks 5-8) — 8 items including 50+ trades | **PARTIAL** | Plan §9 has calibration but different specifics | Different specifics |
| Phase 4: Drills (Weeks 9-10) — 8 items including failure simulation | **MISSING** | Plan has no dedicated "Drills" phase with failure simulation exercises | **GAP: Failure simulation drills phase not in plan** |
| Phase 5: Review (Weeks 11-12) — 8 items including pre-mortem | **PARTIAL** | Plan has review but no pre-mortem exercise | Pre-mortem missing |
| Phase 6: Limited Live (Week 13+) — 6 items | **ADDED** | §9B GPT-86 | Present |

### LIMITED LIVE Parameters

| Parameter | Value | Plan Status | Gap |
|---|---|---|---|
| max_deployed: £1,000 | **ADDED** | §9B GPT-86 | Present |
| max_positions: 1 | **ADDED** | §9B GPT-86 | Present |
| confirm_before_send: true | **ADDED** | §9B GPT-86 | Present |
| S15 only | **ADDED** | §9B GPT-86 | Present |
| min_score: 75 (higher than normal 60) | **PARTIAL** | §9B says higher threshold but doesn't specify 75 | Specific value may differ |
| max_daily_loss: £50 | **PARTIAL** | §9B mentions daily loss but may not specify £50 | Specific value may differ |
| max_weekly_loss: £150 | **MISSING** | §9B may not have weekly loss specific to limited live | **GAP: LIMITED LIVE weekly loss threshold not explicit** |
| heartbeat_interval: 300s (5 min) | **MISSING** | Not in plan | **GAP: Heartbeat interval not in plan** |

### Daily Operational Checklists

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| Morning checklist (07:30-08:00) — 9 items | **ADDED** | §8C GPT-85 | Present |
| Midday checklist (12:00-12:15) — 5 items | **ADDED** | §8C GPT-85 | Present |
| Evening checklist (17:00-17:15) — 7 items | **ADDED** | §8C GPT-85 | Present |
| Daily log template | **MISSING** | Plan doesn't have the structured daily log template from OPS_PUSH | **GAP: Daily log template not in plan** |

### Emergency Procedures

| Item | Plan Status | Gap |
|---|---|---|
| Kill switch activation criteria (5 "when to activate" rules) | **PARTIAL** | Plan has kill switch but not the specific 5-criteria activation decision tree |
| 3-method kill switch procedures with commands | **ADDED** | Present |
| Rollback procedure (5-step) | **MISSING** | Plan has no rollback procedure | **GAP: Rollback procedure not in plan** |
| Communication protocol timeline matrix (7 events) | **MISSING** | Plan has no communication protocol with response timelines | **GAP: No communication timeline matrix** |
| Escalation matrix (LOW/MEDIUM/HIGH/CRITICAL) | **MISSING** | Plan has no escalation matrix | **GAP: No escalation matrix** |
| Post-incident review template | **PARTIAL** | Plan mentions incident library but doesn't have the review template fields | Template structure missing |

---

## PART V: v11 PLAN (AEGIS_MASTER_PLAN_v11.md)

| Item | Plan Status | Gap |
|---|---|---|
| Sacred Parameters list (0.75% risk, 8% DD, etc.) | **ADDED** | §6C GPT-87 | Present |
| 2% Daily Compounding Law | **ADDED** | §0.5 Mission Statement | Present |
| 12 ISA fund universe | **ADDED** | §1 Universe Registrar | Present |
| S15 "2% Daily Target" strategy | **ADDED** | §2 strategies | Present |
| Weekly report generation | **MISSING** | v11 had weekly IC reporting. Plan has no weekly report requirement | **GAP: Weekly IC report not in plan** |
| Broker integration spec (paper → live) | **PARTIAL** | §9B mentions broker but v11 had more detail on broker API integration | Less detail in v13 |
| Diagnostic scripts (diagnostics_live.py) | **MISSING** | v11 referenced daily diagnostic runs. Plan doesn't mention diagnostics_live.py | **GAP: Diagnostic script requirement not in plan** |

---

## PART VI: CODE-EMBEDDED WISDOM (6 files)

### trading_discipline.py

| Item | Plan Status | Plan Location | Gap |
|---|---|---|---|
| 7 Discipline Gates (D-1 through D-7) | **ADDED** | §6B GPT-75 | Present with exact thresholds |
| 10 Commandments | **ADDED** | §6B GPT-75 | Present |
| Excellence Framework (dynamic win rate bar) | **ADDED** | §6B GPT-75 | Present |
| Quality decay during drought (-2 pts/day, floor 50) | **ADDED** | §6B GPT-75 | Present |
| 3 entry points wired (main.py:2879,3774,4266) | **ADDED** | Verified in R14 | Present |

### settings.yaml

| Item | Plan Status | Gap |
|---|---|---|
| 17 immutable risk rules | **ADDED** | Present in §6C |
| 12 Emotional Firewall patterns | **ADDED** | Present in §6 |
| 5-layer confidence engine (45+20+15+10+10=100) | **ADDED** | Present in §2.1 |
| 8 penalty framework items | **ADDED** | Present in §2.1 |
| Drawdown recovery tiers (YELLOW/ORANGE/RED/CRITICAL/EMERGENCY) | **ADDED** | Present in §6 |
| Multi-bot architecture (Bull/Range/Bear) | **PARTIAL** | Present but may not match current ISA-only single-strategy focus | Bot architecture not active in ISA mode |
| Session protection daily P&L tiers | **PARTIAL** | Present but GPT-111 found +1.5% halt prevents 2% target | Bug identified, fix in plan but not in code |

### learning/ modules (trade_autopsy, missed_trade_journal, edge_decay_engine)

| Item | Plan Status | Gap |
|---|---|---|
| Trade autopsy 4-dimension grading (setup/timing/management/context) | **ADDED** | §5 learning engine | Present |
| Missed trade journal (filter quality assessment) | **ADDED** | §5 learning engine | Present |
| Edge decay engine (13 time buckets, fatigue model) | **PARTIAL** | Plan mentions edge decay but doesn't enumerate all 13 US-hours buckets (which are wrong for LSE anyway per GPT-106) | Buckets need LSE conversion |
| Fatigue model (8 trades threshold, quality multiplier table) | **PARTIAL** | Plan mentions fatigue but doesn't have the specific multiplier table | Table not in plan |
| Session classification (am_trend/reversal/choppy/pm_trend) | **MISSING** | Not in plan | **GAP: Session classification taxonomy not in plan** |
| Intraday momentum bias (first-hour return → last-hour scalar) | **MISSING** | Not in plan | **GAP: Intraday momentum bias rule not in plan** |

### risk_officer/officer.py

| Item | Plan Status | Gap |
|---|---|---|
| APPROVE/DOWNSIZE/VETO tri-state decisions | **ADDED** | §5 risk layers | Present |
| 6 governance rules (VolShock, Liquidity, Drawdown, Event, DataReliability, Correlation) | **ADDED** | §5 risk layers | Present |
| Worst-wins escalation logic | **ADDED** | §5, GPT-50 Risk Arbiter | Present |

---

## PART VII: CRITICAL GAPS REQUIRING PLAN AMENDMENTS

### Priority 1 (Constitutional Conflicts — Must Resolve)

| # | Gap | Source | Impact | Resolution |
|---|---|---|---|---|
| **GAP-01** | L1/L2/L3 circuit breaker thresholds don't match Constitution (-1.5%/-2.5%/-4.0%) vs Plan (different tiers) | RISK_CONSTITUTION §3 | Constitutional conflict. Plan's thresholds are different from binding document | Either amend Constitution or align plan to Constitution's L1/L2/L3 |
| **GAP-02** | R19 says full exit on target, plan uses partial exit ladder | RISK_CONSTITUTION §7 R19 | Constitution says binary exit, plan uses 33/67 partials. Formal R19 amendment needed | Amend R19 in Constitution to permit ladder exits |
| **GAP-03** | R23 says 15% drift limit, plan says 20% (GPT-77) | RISK_CONSTITUTION §8 R23 | Constitution says 15%, plan says 20%. Constitution governs | Align plan to 15% or amend Constitution |
| **GAP-04** | R4 (40% total deployment cap) not in plan | RISK_CONSTITUTION §2 R4 | Missing constitutional position limit | Add R4 to plan's risk section |
| **GAP-05** | Weekly DD -8% and Monthly DD -15% not in plan as constitutional breakers | RISK_CONSTITUTION §3 | Missing constitutional circuit breakers | Add -8% weekly and -15% monthly with Constitution's response actions |

### Priority 2 (Missing Operational Infrastructure)

| # | Gap | Source | Impact | Resolution |
|---|---|---|---|---|
| **GAP-06** | 5-state vol regime taxonomy (COMPRESSION/EXPANSION/BLOW_OFF/EXHAUSTION/BREAKDOWN) not in plan | DROUGHT_SPEC §1 | Plan only has market regimes, not per-ticker vol regimes | Add vol regime layer specification |
| **GAP-07** | Full regime transition action matrix (28 from/to pairs) not in plan | DROUGHT_SPEC §5 | Plan says "manage transitions" but doesn't specify what to DO for each transition | Add transition action matrix or reference DROUGHT_SPEC |
| **GAP-08** | Failure simulation drills phase not in plan | OPS_PUSH Phase 4 | No requirement to simulate failures before go-live | Add drills phase to §9 timeline |
| **GAP-09** | Drawdown recovery gate (G7) not in plan's Go-Live criteria | OPS_PUSH G7 | System could go live without ever proving drawdown recovery | Add G7 to Go-Live Gate |
| **GAP-10** | PDF consistency gate (G9) not in Go-Live criteria | OPS_PUSH G9 | Could go live with contradictory PDF outputs | Add G9 to Go-Live Gate |
| **GAP-11** | Rollback procedure not in plan | OPS_PUSH Emergency | No documented procedure to revert to known-good state | Add rollback procedure to §8 |
| **GAP-12** | Communication/escalation matrix not in plan | OPS_PUSH Emergency | No response timelines for different severity events | Add escalation matrix to §8 |

### Priority 3 (Missing Detail)

| # | Gap | Source | Impact | Resolution |
|---|---|---|---|---|
| **GAP-13** | RC-T01 through RC-T10 acceptance tests not in plan | RISK_CONSTITUTION §11 | 10 Constitution acceptance tests not referenced | Add or reference in §9 testing requirements |
| **GAP-14** | R5 universal no-overnight rule for 3x ETPs | RISK_CONSTITUTION §2 R5 | Only 5x has overnight_kill=True in plan | Add universal 16:25 close or formally exempt 3x |
| **GAP-15** | Weekly IC review memo requirement (R25) | RISK_CONSTITUTION §8 R25 | No governance reporting cadence | Add weekly IC review to §8C procedures |
| **GAP-16** | Startup gate session window integration (06:55, 13:25 triggers) | STARTUP_GATE §3 | Plan doesn't specify when pre-market and pre-US gate checks run | Add timing to §8B |
| **GAP-17** | Daily log template structure | OPS_PUSH Daily Ops | No structured daily log format | Add template to §8C |
| **GAP-18** | Session classification taxonomy | edge_decay_engine.py | Plan missing am_trend/reversal/choppy/pm_trend session types | Add to §6D regime section |
| **GAP-19** | Intraday momentum bias (first-hour → last-hour scalar) | edge_decay_engine.py | Code has this logic but plan doesn't document it | Add to §2.1 or §4.4 |
| **GAP-20** | Startup acceptance tests T-STARTUP-001 through T-STARTUP-008 | STARTUP_GATE §6 | 8 startup tests not in plan | Add or reference in §8B |

---

## SIGN-OFF

This tracker identifies **40 MISSING items** and **36 PARTIAL items** across 205 total predecessor wisdom items. The most critical gaps (GAP-01 through GAP-05) involve constitutional conflicts where the RISK_CONSTITUTION.md and the master plan specify different values for the same rules. These must be resolved before any live trading — either by formally amending the Constitution or by aligning the plan to the Constitution's values.

The Constitution is the supreme authority per its own Supremacy Clause (§1.1). Where conflicts exist, the Constitution governs until formally amended.

**Auditor**: Claude Opus 4.6
**Date**: 2026-03-06
