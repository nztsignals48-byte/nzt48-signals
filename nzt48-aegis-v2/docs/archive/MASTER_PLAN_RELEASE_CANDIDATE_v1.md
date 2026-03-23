# MASTER PLAN RELEASE CANDIDATE v1.0
**AEGIS V2 — Institutional Syndicate Board Audit**
**Date:** 2026-03-19 | **Status:** RELEASE CANDIDATE

---

## EXECUTIVE VERDICT

AEGIS V2 is a **real, substantial autonomous trading engine** with production-quality architecture (WAL, 31-check risk arbiter, adaptive Chandelier exits, Ouroboros learning loop). It is NOT vaporware.

However, it has **5 critical bugs that make paper trading results non-transferable to live**, and it has only 20 trades — far below the 100+ minimum for statistical validation.

**Bottom line:** Fix the 8 Priority-0 items (8 days), run 100+ trades through the validation gate, then transition to live. Total time to first live trade: 6-8 weeks.

---

## CRITICAL BUGS (P0 — Fix Immediately)

### BUG 1: Simulation Mode Regime Bypass
**File:** `main.rs:897`
**Impact:** CRITICAL — In simulation mode, risk regime resets to Normal at end of every loop iteration. FLATTEN/HALT gates are bypassed. Paper results accumulate 15 positions vs live max 3. Paper heat reaches 50% vs live max 10%.
**Fix:** Remove the simulation mode regime override. Let paper mode use the same regime logic as live.
**Effort:** 0.5 day
**Label:** PROVEN

### BUG 2: Bar History Lost on Restart
**File:** `engine.rs` (bar_history HashMap is in-memory only)
**Impact:** CRITICAL — On every restart, ATR falls back to 2% of price. Chandelier stops, GARCH residuals, and indicator calculations are wrong for ~500 bars (80+ minutes at 5-min aggregation).
**Fix:** Add BarSnapshot WAL event. Write on session close or periodic checkpoint. Replay on startup.
**Effort:** 2 days
**Label:** PROVEN

### BUG 3: Daily Drawdown Never Resets
**File:** `portfolio.rs` — DailyReset event doesn't update high_water_mark
**Impact:** HIGH — After day 1, high_water_mark stays frozen. Daily drawdown accumulates across days instead of resetting.
**Fix:** In DailyReset handler, set `high_water_mark = current_equity`.
**Effort:** 0.5 day
**Label:** LIKELY

### BUG 4: Paper Spread Veto 4x Looser Than Live
**File:** `risk_arbiter.rs` — paper mode uses 2.0% vs live 0.5%
**Impact:** HIGH — Paper mode accepts entries on wide-spread instruments that live would reject. Inflates paper WR.
**Fix:** Set paper spread veto to 0.5% (same as live).
**Effort:** 0.25 day
**Label:** PROVEN

### BUG 5: Kelly Ramp Threshold Unreachable
**File:** `risk_arbiter.rs` — Kelly ramp maxes at 250 trades
**Impact:** MEDIUM — Daily session produces ~50-100 signals max. Kelly ramp never reaches 100%. Kelly is permanently depressed.
**Fix:** Reduce threshold to 50 trades for full Kelly.
**Effort:** 0.25 day
**Label:** PROVEN

### BUG 6: Mega-Runner Bonus Dead Code
**File:** `exit_engine.rs:518-523` — update_mega_runner() never called
**Impact:** HIGH — Winners that reach 3+ ATR profit don't get wider stops. Cuts off tail capture.
**Fix:** Compute profit_atr in update_tracking(), pass to update_mega_runner().
**Effort:** 0.5 day
**Label:** PROVEN

### BUG 7: Missing Signal Telemetry
**Impact:** CRITICAL — Cannot diagnose why trades win or lose without full indicator snapshots at signal time.
**Fix:** Implement SignalTelemetry schema (see IMPLEMENTATION_MASTER_PLAN.md Phase 2).
**Effort:** 2 days

### BUG 8: Missing Exit Telemetry
**Impact:** CRITICAL — Cannot compare entry vs exit indicators, track rung evolution, or measure exit quality.
**Fix:** Implement ExitTelemetry schema (see IMPLEMENTATION_MASTER_PLAN.md Phase 2).
**Effort:** 2 days

**Total P0: ~8 days of focused work.**

---

## OUROBOROS VERDICT

**Stores intelligence:** YES (PROVEN) — system_memory.json, indicator_intelligence.json, gate_vetoes.ndjson
**Interprets intelligence:** PARTIALLY (LIKELY) — aggregate stats good, causal analysis missing
**Acts on intelligence:** YES (PROVEN) — dynamic_weights.toml, ticker blacklist, indicator gates
**Changes live behavior:** YES (PROVEN) — hot-reload via SIGHUP, bounded by ±15% guardrails

**Ouroboros is genuine adaptive intelligence, not passive analytics.** The guardrails (drift clamping, Bayesian blending, minimum trade counts) are correct and prevent overfitting. The missing piece is per-trade explanation (where Claude integration adds value).

---

## CLAUDE INTEGRATION VERDICT

### USE (8 use cases)
1. **Nightly trade review** — classify wins/losses, explain WHY
2. **Loser/winner diagnosis** — aggregate patterns, suggest fixes
3. **Indicator meaning translation** — human-readable market state
4. **Anomaly interpretation** — explain unusual events
5. **Macro/event classification** — morning briefing
6. **Strategy critique** — weekly parameter review
7. **Code review / PR generation** — development speed
8. **Config suggestions** — bounded parameter tuning with reasoning

### DO NOT USE (2 use cases)
9. **Real-time trade approval** — latency impossible, hallucination risk CRITICAL
10. **Real-time entry timing** — same as above

### ARCHITECTURE
- LLM as analysis layer, deterministic engine as executor
- Claude never on the hot path (<100ms decisions are Rust-only)
- All Claude outputs go through human review before auto-application
- All Claude interactions logged (prompt, response, latency, cost)
- Claude API failures are non-blocking

---

## TELEMETRY SCHEMAS (Exact Rust Structs)

Six telemetry schemas defined in IMPLEMENTATION_MASTER_PLAN.md:
1. **SignalTelemetry** — full indicator snapshot per signal (emitted, vetoed, rejected)
2. **RejectionTelemetry** — why a signal was rejected, with full context
3. **FillQualityTelemetry** — slippage, latency, fill rate
4. **ExitTelemetry** — entry vs exit indicators, rung history, MAE/MFE
5. **MacroStateTelemetry** — hourly macro state snapshots
6. **AnomalyTelemetry** — classified anomaly events

All logged to /app/data/telemetry/*.ndjson, 30-day retention.

---

## CLOCK/CALENDAR FIXES NEEDED

| Issue | Priority | Effort |
|-------|----------|--------|
| US holiday calendar | P1 | 0.5 day |
| Half-day close handling (Christmas Eve, etc.) | P1 | 0.5 day |
| Macro event suppression (FOMC, NFP, CPI) | P1 | 2 days |
| VIX hysteresis (deadband + memory) | P1 | 1 day |
| BST-aware cron offsets | P2 | 0.5 day |

---

## INDICATOR AUDIT SUMMARY

**Keep and rely on:** RVOL, Hurst, ADX, VWAP + sigma, SMA-200/5, volume slope, GARCH, ATR, VIX
**Evaluate:** RSI(2), IBS, bid-ask imbalance (marginal, need 100+ trades to validate)
**Wire up:** HayashiYoshida correlation → exit engine, sector rotation → entry gating
**Don't touch yet:** DQN, Neural Hawkes, Thompson sampling, predictive scoring (premature for current stage)

---

## COMPOUNDING TARGET REALISM

| Target | Annualized | Assessment |
|--------|-----------|------------|
| 0.3% daily net | 113% | SPECULATIVE — world-class, requires very high selectivity |
| 0.5% daily net | 252% | SPECULATIVE — aspirational, not year-round sustainable |
| 0.1% daily net | 28% | LIKELY — achievable with >50% WR and proper risk management |

**Would a serious fund do this?**
- Architecture: YES
- Ouroboros learning: YES
- Current validation: NO (20 trades)
- Current simulation fidelity: NO (regime bypass, loose spread veto)

---

## 100-TRADE VALIDATION GATE

**Criteria (ALL must pass):**
1. Win Rate >= 50%
2. Profit Factor >= 1.3
3. Max Consecutive Losses <= 5
4. Average Rung >= 1.5

**If pass:** Proceed to paper-to-live transition
**If fail:** Diagnose → fix → reset → try again

---

## COMPLETE PRIORITY LIST

### P0 (8 days — before validation gate)
1. Fix simulation mode regime bypass
2. Persist bar history to WAL
3. Fix daily drawdown reset
4. Tighten paper spread veto to 0.5%
5. Fix Kelly ramp threshold (250 → 50)
6. Implement SignalTelemetry
7. Implement ExitTelemetry
8. Wire mega-runner bonus

### P1 (16 days — during validation)
1. RejectionTelemetry
2. FillQualityTelemetry
3. HayashiYoshida → exit engine
4. Bivariate indicator rules
5. Leverage-class differentiation
6. Claude nightly review pipeline
7. Ouroboros recommendation tracking
8. Macro event suppression (FOMC, NFP)
9. VIX hysteresis
10. US holiday calendar
11. Half-day closes
12. Velocity check optimization
13. Cost basis leak fix
14. MAE/MFE for open positions

### P2 (14.5 days — after gate passes)
1. Session-regime interaction matrix
2. Pattern confidence decay
3. Claude macro briefing
4. Claude strategy critique (weekly)
5. Dead code cleanup (DQN, Neural Hawkes stubs)
6. BST-aware cron offsets
7. Google Sheets tabs (15 tabs specified)
8. Claude MCP data access layer
9. Sector rotation gating
10. AnomalyTelemetry

---

## TIMELINE

```
Week 1-2:  P0 fixes (8 days)
Week 2-6:  100-trade validation (P1 in parallel)
Week 6:    Gate assessment
Week 7-8:  Paper-to-live transition (if gate passes)
Week 8+:   P2 improvements + first live trades
```

**First live trade: ~6-8 weeks from 2026-03-19 = early May 2026.**

---

## ARTIFACTS PRODUCED

1. `REPO_MAP.md` — Complete file inventory with annotations
2. `RUNTIME_ARTIFACT_MAP.md` — All runtime files, flows, and state management
3. `IMPLEMENTATION_MASTER_PLAN.md` — Full 10-phase institutional audit with schemas, taxonomies, and action items
4. `MASTER_PLAN_RELEASE_CANDIDATE_v1.md` — This document (condensed, actionable)

---

*Institutional Syndicate Board — CTO, CRO, CIO, Head of Quant Research, Head of Execution, Head of Production/SRE, Head of Autonomous Intelligence Design*
*No flattery. No vague praise. No decorative sophistication.*
*Every claim labeled. Every fix prioritized. Every schema specified.*
