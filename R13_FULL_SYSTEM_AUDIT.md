# AEGIS Master Plan v13.10 — Round 13 Full System Audit

**Auditor**: Claude Opus 4.6 (All 4 Personas: Chief Quant, Lead Architect, CRO, Academic)
**Date**: 2026-03-05
**Scope**: Full codebase (131,254 LOC, 298 files) + ALL predecessor plans (116 docs, 3.5M+ total) + 7 institutional procedures + operational wisdom extraction
**Method**: Cross-reference every trading principle from predecessor systems against v13 plan and live code. Identify missing operational wisdom. Apply 4-persona analysis to each finding.

---

## EXECUTIVE SUMMARY

The AEGIS Master Plan v13.10 is an extraordinary *theoretical architecture* — but it is **theory without operations**. The predecessor documents (OPS_PUSH, Risk Constitution, Drought Spec, Signal Truth Table, Paper Launch Audit, v11 plan) contained critical operational muscle that was LOST in the v11→v13 transition:

- **32 operational principles** extracted from predecessor systems
- **25 new amendments** (GPT-75 through GPT-99)
- **8 P0, 9 P1, 8 P2**

The single most important finding: the codebase already contains a `TradingDisciplineEngine` with the exact principles the user asked about ("no emotion", "don't force trades", "stay silent if nothing qualifies") — but **the master plan doesn't reference it AT ALL**.

---

# PART I: THE 10 COMMANDMENTS OF NZT-48
## (Extracted from TradingDisciplineEngine + Predecessor Systems)

These principles exist scattered across the codebase and old docs. They must be consolidated and enshrined in the master plan as inviolable law.

### Commandment 1: NO TRADE IS BETTER THAN A BAD TRADE
**Source**: `core/trading_discipline.py` line 8
**Academic**: Taleb (2007) — "The cost of NOT trading is zero. The cost of a bad trade is real capital destruction."
**Code reality**: `MIN_SETUP_QUALITY = 65`. Below this, the system stays flat. No exceptions.

### Commandment 2: THE SYSTEM MUST NEVER BE FORCED INTO TRADING
**Source**: `core/trading_discipline.py` line 9
**Academic**: Barber & Odean (2000) — "The most active traders underperform by 6.5%."
**Code reality**: `MAX_TRADES_PER_DAY = 4`. `MAX_NO_TRADE_DAYS_BEFORE_REVIEW = 5` — the system doesn't panic if no trades for 5 consecutive days. It records "discipline, not inactivity."

### Commandment 3: CASH IS A POSITION
**Source**: `core/trading_discipline.py` SHOCK regime gate
**Reality**: When regime = SHOCK or VIX > 35, the system does literally nothing. It logs "Sit on hands. Cash is a position. The cost of not trading is zero." This is not a failure state — it's the correct state.

### Commandment 4: CAPITAL PRESERVATION IS THE FIRST RULE OF COMPOUNDING
**Source**: `core/trading_discipline.py` line 10
**Academic**: Buffett — "Rule 1: Never lose money. Rule 2: Never forget Rule 1."
**Code reality**: Daily loss halt at -3%. Weekly halt at -5%. Total DD halt at -15%. These are constitutional and immutable.

### Commandment 5: WHEN IN DOUBT, KILL IT
**Source**: `archive/docs/OPS_PUSH_92_TO_100.md`, Incident Response Playbook
**The Asymmetry**: Cost of false positive (missed day) = 2% of daily target. Cost of false negative (uncontained incident on 3x-5x leverage) = potentially catastrophic. Expected value of "kill first, investigate second" is overwhelmingly positive.

### Commandment 6: EACH TRADE MUST STAND ON ITS OWN MERIT
**Source**: `core/trading_discipline.py` — Samuelson (1963) fallacy of large numbers
**Reality**: The system evaluates each trade independently. A winning streak doesn't justify a bad setup. A losing streak doesn't mean the next trade is owed to you.

### Commandment 7: TODAY'S EXCELLENCE IS TOMORROW'S AVERAGE
**Source**: `core/trading_discipline.py` EXCELLENCE_WIN_RATE, IMPROVEMENT_RATE
**Code reality**: When rolling win rate exceeds the excellence bar (starting at 55%), the bar is RAISED by 1%. The system never gets complacent. This is the ratchet mechanism that prevents edge decay through complacency.

### Commandment 8: IF THERE ARE 5 TOP TRADES, MAKE THEM ALL
**Source**: Strategy architecture in `main.py` — ALL 16 strategies fire independently
**Code reality**: Strategies run in parallel. If S1, S2, S15, and S16 all produce qualifying signals simultaneously, ALL of them enter the pipeline. The system does NOT artificially restrict to 1 trade when 5 are available. Portfolio-level risk limits (3% heat cap, correlation brake, max concurrent positions) are the ONLY governors — not artificial trade-count limits.
**Important nuance**: S15 is self-limited to 1/day (the BEST candidate). But other strategies can fire alongside S15 in the same scan cycle.

### Commandment 9: IF THERE ARE NO QUALIFIED TRADES, STAY SILENT
**Source**: `core/trading_discipline.py` — "the market owes us nothing"
**Code reality**: No-trade days are logged as discipline, not failure. The system has a drought state machine (DROUGHT_NONE → DROUGHT_WATCH → DROUGHT_ACTIVE → DROUGHT_CRITICAL) that monitors silence WITHOUT forcing trades. Quality decay is bounded — threshold NEVER drops below 50. The system would rather sit flat for a week than take a 49-quality trade.

### Commandment 10: NO EMOTION, NO OVERRIDE, ZERO EXCEPTIONS
**Source**: `qualification/go_nogo.py` — "Zero emotional firewall overrides"
**Code reality**: The Go-Live Gate tracks any time a human overrode the system's decision and counts it as a FAILURE criterion. Zero overrides in the entire paper trading phase is a non-negotiable requirement for going live.

---

# PART II: OPERATIONAL WISDOM LOST IN v11→v13 TRANSITION

## GPT-75: Trading Discipline Engine Integration (P0-CRITICAL)
**The Bug**: The master plan describes 15 risk controls, 33 gates, Kelly sizing, and ML meta-labels — but NEVER ONCE references `core/trading_discipline.py` which contains the 7-gate emotional/behavioral framework that is arguably more important than all 33 technical gates combined.

**What it contains**:
1. Daily loss limit gate (-3% = done for the day)
2. Cooldown gate (2 hours after 4 consecutive losses)
3. Max trades gate (4/day — prevents overtrading)
4. Setup quality gate (65 minimum, 50 absolute floor)
5. Edge expectancy gate (min 0.10R expected)
6. SHOCK regime gate (absolute block)
7. VIX extreme gate (>35 = absolute block)

**Fix**: Add TradingDisciplineEngine as Section 6B in the master plan. Its 7 gates must be the FIRST check in the signal pipeline — before any technical indicator analysis.

**Hours**: 2h (plan documentation) + 1h (verify wiring in main.py)

---

## GPT-76: Risk Constitution Supremacy Clause (P0)
**Source**: `archive/annexes/RISK_CONSTITUTION.md`
**What's missing**: The master plan treats risk rules as implementation details. The Risk Constitution treats them as constitutional law with:
- A formal amendment procedure (written IC submission, 5-day review, unanimous consent)
- A supremacy clause (Constitution supersedes ALL code, config, operator instructions, learning engine output)
- Violation severity taxonomy (CRITICAL/MAJOR/MINOR with escalating response)
- Append-only incident library that cannot be modified or deleted

**Fix**: Add a "Risk Constitution" preamble to Section 6 establishing constitutional hierarchy and amendment procedures.

**Hours**: 1h

---

## GPT-77: Learning Engine Constitutional Bounds (P0)
**Source**: `archive/annexes/RISK_CONSTITUTION.md` R21-R25
**What's missing**: The ML meta-model and adaptive engines can currently adjust parameters without bounds. The Risk Constitution specifies:
- R21: Learning may only adjust within ±20% of baseline
- R22: Meta-learner CANNOT adjust position limits, drawdown levels, leverage rules, stop rules, execution timing, or any constitutional rule
- R23: If any learning-adjusted parameter drifts >15% from baseline → DEFENSIVE mode, ALL parameters reverted to defaults
- R24: Minimum 100 resolved trade outcomes before ANY parameter adjustment
- R25: All learning adjustments documented in weekly IC review memo

**Fix**: Add "Constitutional Bounds on Adaptive Intelligence" subsection to Section 5.

**Hours**: 2h

---

## GPT-78: Startup Readiness Gate (P0)
**Source**: `archive/annexes/STARTUP_READINESS_GATE_SPEC.md`
**What's missing**: The system currently starts trading without validating its own integrity. The Startup Gate runs 8 checks:
1. Database connectivity and table existence
2. Redis connectivity and Chandelier state
3. Data feed health (all 12 tickers returning fresh data)
4. Kill switch status (must be OFF)
5. Circuit breaker state (must not be HALTED from previous session)
6. Disk space (>20% free)
7. Memory (>500MB free)
8. Time synchronization (NTP drift <5 seconds)

Three-tier output: READY / DEGRADED (monitoring only, no trades) / HALTED.
Re-validates at every session window transition (06:55 UK, 13:25 UK).

**Fix**: Add Section 8B "Startup Readiness Gate" — 8-check pre-flight with 3-tier output.

**Hours**: 3h (implementation) + 1h (plan)

---

## GPT-79: Drought-Regime Contradiction Detection (P0)
**Source**: `archive/annexes/REGIME_DROUGHT_SPEC.md`
**What's missing**: 5 self-consistency rules that detect when the system's internal state is contradictory:
- C1: Market TRENDING + drought active = CONTRADICTION (if trending, why no signals?)
- C2: Vol regime EXPANSION + drought active = CONTRADICTION
- C3: Market COMPRESSION + no drought = NORMAL
- C4: RANGE_BOUND + drought watch = NORMAL
- C5: SHOCK + no drought = CONTRADICTION

When a contradiction is detected, it means something is broken — a data feed is stale, a classifier is stuck, or a gate is miscalibrated. This is the "smoke detector" that catches silent failures.

**Fix**: Add drought-regime contradiction detection to the scan health monitor.

**Hours**: 2h

---

## GPT-80: Regime Flapping Protection (P0)
**Source**: `archive/annexes/REGIME_DROUGHT_SPEC.md`
**What's missing**: "If market_regime changes more than 3 times in 10 minutes, enter REGIME_FLAPPING state. Hold current positions, no new entries, size = 0.25x."

This is DIFFERENT from VIX hysteresis (GPT-46). VIX hysteresis prevents oscillation at VIX thresholds. Regime flapping protection catches rapid back-and-forth regime changes from ANY cause (high-impact news, data feed errors, classifier instability).

**Fix**: Add REGIME_FLAPPING as a fourth regime state alongside NORMAL, REDUCE, HALT.

**Hours**: 1h

---

## GPT-81: Post-Recovery Ramp-Up (P0)
**Source**: `archive/annexes/REGIME_DROUGHT_SPEC.md`
**What's missing**: After RISK_OFF clears: resume at 0.25x size for 30 minutes, then ramp to normal. After SHOCK clears: resume at 0.25x for 60 minutes, then ramp to normal.

Currently, when a regime transitions from SHOCK→NORMAL, the system immediately trades at full size. This is dangerous — the first "normal" after a shock could be a dead cat bounce.

**Fix**: Add ramp-up schedule to regime transition handler.

**Hours**: 1h

---

## GPT-82: Regime Stuck Detection (P0)
**Source**: `archive/annexes/REGIME_DROUGHT_SPEC.md`
**What's missing**: "If both classifiers return same regime for 24+ hours, the classifier may be stuck."

A stuck classifier that silently returns the same value indefinitely looks like a healthy system. But if the market moved 5% in a day and the regime still says RANGE_BOUND, the classifier has failed silently.

**Fix**: Track `regime_last_changed_utc`. Alert if unchanged for >24h of market time.

**Hours**: 0.5h

---

## GPT-83: Kill-First Asymmetry Principle (P1)
**Source**: Incident Response Playbook
**What's missing**: The simple statement: "When in doubt, activate the kill switch. A missed trading day costs at most 2%. An uncontained incident costs multiples. UNCERTAIN always resolves to KILL."

The plan has elaborate risk state machines but never states this asymmetry. Complex systems fail in complex ways. Having a simple default-to-safety rule is more valuable than a perfect risk state machine.

**Fix**: Add the Kill-First Asymmetry as Rule 0 of the Risk Constitution.

**Hours**: 0h (text only)

---

## GPT-84: Evidence Preservation Protocol (P1)
**Source**: Incident Response Playbook
**What's missing**: Mandatory "photograph the crime scene" step BETWEEN incident detection and corrective action. "Do NOT restart services until evidence is preserved. A restart destroys in-memory state and may rotate logs."

Natural instinct during an incident: fix it NOW. The correct procedure: preserve state FIRST, fix SECOND. Without this, post-mortem analysis is impossible.

**Fix**: Add evidence preservation as mandatory phase in incident response.

**Hours**: 0.5h

---

## GPT-85: Daily Operational Checklists (P1)
**Source**: `archive/docs/OPS_PUSH_92_TO_100.md`
**What's missing**: The v13 plan is purely architecture. It has ZERO operational content. The OPS_PUSH had concrete 3-times-daily checklists:

**Morning (07:30-08:00 UK)**: Container health, overnight errors, data feeds, disk/memory, PDF review
**Midday (12:00-12:15 UK)**: Open positions, scan health, edge outcomes, HALT check
**Evening (17:00-17:15 UK)**: Daily P&L, PDF verification, Telegram log, resource usage

**Fix**: Add Section 8C "Daily Operational Procedures" with concrete checklists.

**Hours**: 1h

---

## GPT-86: LIMITED LIVE Transition Plan (P1)
**Source**: `archive/docs/OPS_PUSH_92_TO_100.md`
**What's missing**: The plan goes from "63 MTRL days of paper" straight to "live trading." There is no intermediate stage. The OPS_PUSH specified:

**LIMITED LIVE Parameters**:
- Max deployed capital: £1,000 (not £10,000)
- Max positions: 1
- Max daily loss: £50
- Max weekly loss: £150
- Strategy: S15 only
- Order type: LIMIT only (no market orders)
- `confirm_before_send: true` (human approval before each trade)
- Duration: minimum 2 weeks

**Fix**: Add "Limited Live" as a mandatory phase between paper and full live.

**Hours**: 1h (plan)

---

## GPT-87: Sacred Parameters List (P1)
**Source**: `AEGIS_MASTER_PLAN_v11.md`
**What's missing**: v11 had an explicit "do NOT change these" list:
- Risk per trade: 0.75% (battle-tested, Kelly-aligned)
- S15 max 1 signal/day (core discipline)
- ATR stop multiplier: 1.5x (proven across 413+ trades)
- Power Hour +15% boost (Heston et al. 2010)
- SHAP rank drift threshold: >5 positions
- CUSUM threshold: 3.0 (Page 1954)
- HMM confirmation lag: 3 days

v13 doesn't have an equivalent "these are sacred" list. Future reviewers may inadvertently propose changing a battle-tested parameter.

**Fix**: Add Table E "Sacred Constants — Do Not Modify" to Section 10.

**Hours**: 0.5h

---

## GPT-88: Multi-Trade Simultaneous Execution Rules (P1)
**Source**: Code analysis of `main.py` + all 16 strategies
**What's missing**: The plan doesn't clearly state the multi-trade architecture:

1. **ALL 16 strategies fire independently in every scan cycle.** There is no mutual exclusion.
2. **If 5 strategies produce qualifying signals, all 5 enter the pipeline.** The downstream gauntlet filters, not the strategies.
3. **S15 is self-limited to 1/day (BEST candidate). Other strategies have their own limits.**
4. **Portfolio-level governors are the ONLY trade-count limiter**: max concurrent positions per bot (BULL: 7, RANGE: 3, BEAR: 2), 3% portfolio heat cap, correlation brake.
5. **The system can hold positions from multiple strategies simultaneously** — S15 long QQQ3.L + S13 long NVD3.L + S4 long TSL3.L is a valid state if all pass the gauntlet.

**Fix**: Add explicit multi-trade architecture section documenting simultaneous execution rules.

**Hours**: 0.5h

---

## GPT-89: Drought State Machine (P1)
**Source**: `archive/annexes/REGIME_DROUGHT_SPEC.md` + `core/trading_discipline.py`
**What's missing**: The plan mentions "no-signal escalation" but doesn't capture the full state machine:

```
DROUGHT_NONE → 10 dry cycles → DROUGHT_WATCH → 20 → DROUGHT_ACTIVE → 60 → DROUGHT_CRITICAL
```

Key rules:
- Counter resets ONLY on signal that passes ALL gates AND is sent AND is not deduped
- A signal generated but blocked does NOT reset the counter
- At DROUGHT_CRITICAL: quality threshold decays by 2 pts/day, but NEVER below 50
- Message: "The market owes us nothing"
- After 5 no-trade days: review triggered BUT with instruction "do NOT lower standards just to trade"

**Fix**: Add drought state machine to Section 6.

**Hours**: 1h

---

## GPT-90: Circuit Breaker Persistence Across Restarts (P1)
**Source**: `archive/annexes/RISK_CONSTITUTION.md`
**What's missing**: "Circuit breaker state MUST persist to disk. A system restart does not reset the circuit breaker level."

This prevents a dangerous exploit: restarting the Docker container to clear circuit breakers after a drawdown. Currently, a `docker compose restart nzt48` could theoretically reset the drawdown cascade level.

**Fix**: Persist circuit breaker state to SQLite. Load on startup. Only reset via explicit daily reset at session boundary.

**Hours**: 1h

---

## GPT-91: SystemState Health Machine (READY/DEGRADED/HALTED) (P1)
**Source**: `archive/docs/PAPER_LAUNCH_AUDIT.md` + Startup Readiness Spec
**What's missing**: The plan has a Risk State Machine (NORMAL/REDUCE/HALT) for risk decisions but NO health state machine for system integrity. These are different concerns:

- **Risk State**: Should we trade? (market conditions)
- **Health State**: CAN we trade? (system integrity)

A system can be Risk=NORMAL (market is fine) but Health=DEGRADED (data feed is stale). The DEGRADED tier allows monitoring without trading — maintaining situational awareness without risking capital.

**Fix**: Add SystemState health machine separate from Risk State Machine.

**Hours**: 1h

---

## GPT-92: Signal Log Field Integrity Audit (P2)
**Source**: `archive/docs/SIGNAL_TRUTH_TABLE.md`
**What's missing**: The Signal Truth Table found that of 80+ canonical fields, the signal log populates only 26 — and many are always 0.0 or empty string:
- `spread_bps`: always 0.0 (never populated from `_SPREAD_BPS` table)
- `rsi`, `adx`, `bb_width`: always 0.0
- `risk_officer_decision`: always "APPROVE"
- `strategy_tag`: often empty
- `regime_tag`: sometimes uses legacy labels

**Fix**: Add signal log integrity check to daily health monitoring. Alert if >10% of fields are default/empty.

**Hours**: 1h

---

## GPT-93: Deterministic Reproducibility Requirement (P2)
**Source**: `archive/docs/SIGNAL_TRUTH_TABLE.md`
**What's missing**: "Given the same run_id, git_hash, config_hash, and universe_hash, the exact same signals MUST be reproducible." Every signal must carry provenance: `run_id`, `git_hash`, `config_hash`, `universe_hash`.

**Fix**: Add provenance fields to signal metadata. Store git hash at startup.

**Hours**: 1h

---

## GPT-94: Fatigue Detection (P2)
**Source**: `learning/edge_decay_engine.py`
**What's missing**: The Edge Decay Engine models decision fatigue:
- 0-4 trades = 100% quality
- 5 trades = 98%, 6 = 95%, 7 = 90%, 8 = 85%
- 9 = 78%, 10 = 70%, 11 = 60%, 12 = 50%
- Beyond 12: -5% per trade, floor 10%
- SEVERE FATIGUE below 70%: "Consider stopping for the day"

This is research-backed (Danziger, Levav & Avnaim-Pesso 2011 — judicial decision fatigue). The master plan doesn't reference this module or its implications.

**Fix**: Document fatigue model in Section 6B alongside TradingDisciplineEngine.

**Hours**: 0.5h

---

## GPT-95: Alpha Curve — Time-of-Day Edge Decay (P2)
**Source**: `learning/edge_decay_engine.py`
**What's missing**: The empirical alpha scalars per 30-minute bucket:
- 09:30-10:00: 1.00 (peak alpha)
- 11:30-13:00: 0.50 (dead zone — HALF the edge)
- 15:30-16:00: 0.50 (normally low, but boosted by first-hour momentum if present)

The plan mentions "lunch dead zone" but doesn't quantify the edge decay curve or use it for position sizing.

**Fix**: Document alpha curve and reference EdgeDecayEngine in Section 4.

**Hours**: 0.5h

---

## GPT-96: Trade Autopsy as Mandatory Feedback Loop (P2)
**Source**: `learning/trade_autopsy.py`
**What's missing**: Every closed trade gets a 4-dimension autopsy (Setup, Timing, Management, Market Context) with grades 0-100. The plan mentions "Exit Attribution" (A-6) but doesn't reference the full autopsy system that already exists and feeds back into confidence adjustments.

Key insight: If average autopsy grade drops below 40 for a strategy, it gets -10 confidence. Below 50: -5. Above 75: +5. This is an automatic quality feedback loop.

**Fix**: Reference TradeAutopsyEngine in Section 5 and connect it to the Exit Attribution system.

**Hours**: 0.5h

---

## GPT-97: Missed Trade Journal (P2)
**Source**: `learning/missed_trade_journal.py`
**What's missing**: The system tracks every signal that qualified but was BLOCKED by filters, then tracks what WOULD have happened. This answers the critical question: "Are our filters helping or hurting?"

If missed trades have HIGHER win rates than taken trades, the filters are actively destroying edge and need loosening. The plan has no concept of filter effectiveness validation.

**Fix**: Reference MissedTradeJournal in Section 5 as a filter validation mechanism.

**Hours**: 0.5h

---

## GPT-98: Weekly Signal Quality Report (P2)
**Source**: `AEGIS_MASTER_PLAN_v11.md`
**What's missing**: v11 specified a Sunday 20:00 report containing:
- Win rate by strategy (S15 this week vs 4-week rolling)
- Win rate by regime
- Dry day count
- ML health (AUC, SHAP stability, feature drift)
- Compounding tracker (actual geometric return vs 2% target)

**Fix**: Add weekly report specification to Section 8.

**Hours**: 0.5h

---

## GPT-99: Parameter Fragmentation Audit (P2)
**Source**: `NZT48_CONTRADICTION_AUDIT_V5.md`
**What's missing**: The same parameter exists in 4+ places with different values:
- Ticker universe: 4 competing sources (12 vs 19 tickers)
- Spread defaults: 5 bps vs 20 bps depending on import path
- Stop-loss ATR multiplier: 9 different definitions
- ATR% minimum: 4 different values

**Fix**: Add a "Parameter SSOT Audit" requirement — every parameter must have exactly ONE canonical source. Duplicates must delegate to the SSOT.

**Hours**: 1h

---

# PART III: AMENDMENT REGISTER (GPT-75 through GPT-99)

| # | Title | Severity | Hours | Phase |
|---|-------|----------|-------|-------|
| GPT-75 | Trading Discipline Engine integration (10 Commandments + 7 gates) | P0 | 3h | A |
| GPT-76 | Risk Constitution supremacy clause + amendment procedure | P0 | 1h | A |
| GPT-77 | Learning Engine constitutional bounds (R21-R25) | P0 | 2h | A |
| GPT-78 | Startup Readiness Gate (8-check pre-flight, 3-tier output) | P0 | 4h | A |
| GPT-79 | Drought-Regime contradiction detection (5 consistency rules) | P0 | 2h | A |
| GPT-80 | Regime Flapping Protection (3+ changes in 10 min = hold) | P0 | 1h | A |
| GPT-81 | Post-Recovery Ramp-Up (0.25x for 30-60 min after shock) | P0 | 1h | A |
| GPT-82 | Regime Stuck Detection (24h+ unchanged = alert) | P0 | 0.5h | A |
| GPT-83 | Kill-First Asymmetry Principle (Rule 0 of Risk Constitution) | P1 | 0h | A |
| GPT-84 | Evidence Preservation Protocol (photograph before fix) | P1 | 0.5h | A |
| GPT-85 | Daily Operational Checklists (morning/midday/evening) | P1 | 1h | A |
| GPT-86 | LIMITED LIVE Transition Plan (£1K, 1 position, human confirm) | P1 | 1h | A |
| GPT-87 | Sacred Parameters List (Table E — Do Not Modify) | P1 | 0.5h | A |
| GPT-88 | Multi-Trade Simultaneous Execution Rules | P1 | 0.5h | A |
| GPT-89 | Drought State Machine (full cycle-based specification) | P1 | 1h | A |
| GPT-90 | Circuit Breaker Persistence Across Restarts | P1 | 1h | A |
| GPT-91 | SystemState Health Machine (READY/DEGRADED/HALTED) | P1 | 1h | B |
| GPT-92 | Signal Log Field Integrity Audit | P2 | 1h | B |
| GPT-93 | Deterministic Reproducibility Requirement | P2 | 1h | B |
| GPT-94 | Fatigue Detection Model | P2 | 0.5h | B |
| GPT-95 | Alpha Curve — Time-of-Day Edge Decay | P2 | 0.5h | B |
| GPT-96 | Trade Autopsy as Mandatory Feedback Loop | P2 | 0.5h | B |
| GPT-97 | Missed Trade Journal Filter Validation | P2 | 0.5h | B |
| GPT-98 | Weekly Signal Quality Report | P2 | 0.5h | B |
| GPT-99 | Parameter Fragmentation SSOT Audit | P2 | 1h | B |

**New Phase A hours**: +19.5h (GPT-75 through GPT-90)
**New Phase B hours**: +6.5h (GPT-91 through GPT-99)
**Phase A revised total**: 65h (R12) + 19.5h = **84.5h**

---

# PART IV: UPDATED PHASE A — COMPLETE (GPT-01 through GPT-99)

```
PHASE A — EXISTENTIAL (must complete before ANY live trading):

  TIER 1: CONSTITUTIONAL (cannot trade without these)
    A-1:  ISA Eligibility Gate — Three-Key Safe [P0, 8h]
    A-11: Immutable Risk Rules __setattr__ guard [P0, 1h] (GPT-54)
    A-21: Risk Constitution supremacy clause [P0, 1h] (GPT-76)
    A-22: Trading Discipline Engine — 10 Commandments + 7 gates [P0, 3h] (GPT-75)

  TIER 2: SIGNAL INTEGRITY (signals must be trustworthy)
    A-2:  Signal Queue + Consumer + exception fix [P0, 8.5h] (incl GPT-39, GPT-55)
    A-12: Sanity gates on S15/S16 + fail-CLOSED [P0, 2h] (GPT-57)
    A-8:  EV Gate rename + threshold fix [P0, 2h] (GPT-44)
    A-9:  CDaR Historical Simulation VaR [P0, 3h] (GPT-43)

  TIER 3: REGIME INTEGRITY (regime must be stable)
    A-3:  Regime State Machine + VIX hysteresis + HALT split [P0, 9h] (incl GPT-37, GPT-56)
    A-23: Regime Flapping Protection [P0, 1h] (GPT-80)
    A-24: Post-Recovery Ramp-Up [P0, 1h] (GPT-81)
    A-25: Regime Stuck Detection [P0, 0.5h] (GPT-82)
    A-26: Drought-Regime Contradiction Detection [P0, 2h] (GPT-79)

  TIER 4: RISK INTEGRITY (risk engine must be accurate)
    A-5:  Risk State Machine + Emergency Flatten + Risk Arbiter [P0, 9h] (incl GPT-40, GPT-50, GPT-67)
    A-15: VirtualTrader lock contention fix [P0, 3h] (GPT-60)
    A-16: DynamicSizer SHOCK_RECOVERY session fix [P0, 1h] (GPT-61)
    A-27: Learning Engine constitutional bounds [P0, 2h] (GPT-77)
    A-28: Startup Readiness Gate [P0, 4h] (GPT-78)

  TIER 5: DATA INTEGRITY (data must be clean)
    A-4:  Phantom Ticker Purge + config SSOT [P0, 3h] (incl GPT-68)
    A-13: ML Regime/Ticker map alignment [P0, 1h] (GPT-58)
    A-14: ML SHAP feature save fix [P0, 2h] (GPT-59)

  TIER 6: EXIT INTEGRITY (must be able to exit correctly)
    A-6:  Exit Reason Enum + Attribution Record [P0, 4h]
    A-7:  Shadow Markout Tracker [P0, 4h]
    A-10: Exit Loop Decoupling (10s exit eval) [P0, 3h] (GPT-49)

  TIER 7: OPERATIONAL READINESS
    A-17: Kelly rolling window stats fix [P1, 2h] (GPT-62)
    A-18: Chandelier Redis TTL 72h [P1, 0.5h] (GPT-64)
    A-19: Circuit breaker reset time guard [P1, 1h] (GPT-66)
    A-20: SessionProtection profit halt raise [P2, 0.5h] (GPT-71)
    A-29: Kill-First Asymmetry principle [P1, 0h] (GPT-83)
    A-30: Evidence Preservation protocol [P1, 0.5h] (GPT-84)
    A-31: Daily Operational Checklists [P1, 1h] (GPT-85)
    A-32: LIMITED LIVE Transition Plan [P1, 1h] (GPT-86)
    A-33: Sacred Parameters List (Table E) [P1, 0.5h] (GPT-87)
    A-34: Multi-Trade Execution Rules doc [P1, 0.5h] (GPT-88)
    A-35: Drought State Machine [P1, 1h] (GPT-89)
    A-36: Circuit Breaker Persistence [P1, 1h] (GPT-90)

  TOTAL: 84.5 hours
```

---

# PART V: THE COMPLETE TRADING PHILOSOPHY

Synthesized from ALL 116 documents and 298 Python files:

**THE SYSTEM IS A PATIENT SNIPER, NOT A MACHINE GUN.**

1. It scans continuously (every 60 seconds, 24/7) but trades rarely (1-4 times per day maximum)
2. It can see 40+ tickers but picks only the best 1-5 setups
3. It has 16 loaded weapons (strategies) but fires only when conditions are perfect
4. It would rather sit flat for a week than take one bad trade
5. It celebrates no-trade days as discipline, not failure
6. It gets better over time (excellence ratchet, trade autopsy, filter validation)
7. It knows when its own edge is decaying (CUSUM alpha reaper, edge decay engine)
8. It knows when its own state is broken (drought-regime contradictions, stuck detection)
9. It kills first and investigates second (asymmetry principle)
10. It never overrides itself (zero emotional firewall overrides = go-live criterion)

**The 2% daily target is a HORIZON GOAL, not a daily obligation.** Some days will yield 0% (no qualifying trades). Some days will yield 5%+ (a runner on a 3x ETP). The GEOMETRIC MEAN of the equity curve, over 252 trading days, must approximate 2% compounded daily — but any individual day can be zero.

---

## SIGN-OFF

Round 13 produced 25 amendments (GPT-75 through GPT-99), adding 19.5h to Phase A (now 84.5h total). The most critical additions are operational wisdom that was present in predecessor systems but lost during the v11→v13 transition: the Trading Discipline Engine (10 Commandments), the Risk Constitution hierarchy, the Startup Readiness Gate, and the Kill-First Asymmetry principle.

The plan version is now v13.11 pending application of GPT-75 through GPT-99.

**Total amendments across all rounds**: 99 (GPT-01 through GPT-99)
**Total review rounds**: 13 (3 AI models, 4 personas, 116 source documents, 131K LOC)

**Auditor**: Claude Opus 4.6
**Date**: 2026-03-05
