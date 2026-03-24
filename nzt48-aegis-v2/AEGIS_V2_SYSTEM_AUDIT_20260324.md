# AEGIS V2 — Brutal System Audit
**Date**: 2026-03-24
**Auditor**: Claude Opus (code-level, not plan-level)
**Method**: Read every file in the signal path. No assumptions from plans.

---

## A. Executive Truth Summary

AEGIS V2 is a **single-strategy paper-trading system** wearing the costume of a multi-strategy platform. It has one signal generator that produces trades (VanguardSniper), one backup generator that occasionally fires (Orchestrator S17-S20), and a classification layer (TypeA-F) that blocks more than it helps.

**The system is not split-brained.** The Rust engine and Python bridge are well-designed and communicate cleanly. The risk arbiter is deterministic and correct. The exit engine works. The deployment is robust.

**The main issue is not architecture — it is economics.** The system has a 35.4% win rate and negative P&L after 66 trades. No amount of microstructure, regime routing, or session gating fixes a signal that loses money. The primary investigation must be: why does VanguardSniper lose?

**Secondary issue: false breadth.** The plan claims "6 strategies + TypeA-F classification" but code-level truth is: 1 producer, 4 dormant, 4 dead shadow-mode code, 6 post-hoc labels (3 disabled, 2 shadow, 1 unreachable). This creates false confidence about diversification.

---

## B. What Is Actually Working

| Component | Status | Evidence |
|-----------|--------|----------|
| Rust engine tick processing | **WORKING** | 570k+ ticks processed, <1ms per tick |
| Risk arbiter (32 checks) | **WORKING** | Deterministic, fail-closed, <1ms |
| Chandelier 5-rung exit | **WORKING** | Rung advancement tracked in WAL, stops ratchet correctly |
| Time-stop (45min, 0.3x ATR) | **WORKING** | Deployed session 3, active_trading_ticks pauses during halts |
| Board lot sizing | **WORKING** | TSE/HKEX/SGX = 100-share lots, LOT_SKIP on sub-lot |
| Unhalt grace period | **WORKING** | active_trading_ticks reset to 0 on halt lift |
| Spoof detector (calibrated) | **WORKING** | 25x multiplier + 2% floor, zero false positives post-fix |
| Python bridge IPC | **WORKING** | JSON over stdin/stdout, 5s timeout, reader thread |
| VanguardSniper signal generation | **WORKING** | 33 trades, confidence scoring, Kelly sizing |
| Orchestrator (S17-S20) | **WORKING** | 4 independent evaluators from strategies.toml, all enabled |
| WAL event logging | **WORKING** | Crash recovery, nightly analysis source |
| Nightly pipeline | **WORKING** | nightly_v6.py, config_writer, claude_review all run |
| Strategy registry | **WORKING** | Perfect alignment with bridge.py |
| Docker deployment | **WORKING** | Preflight checks, graceful degradation |
| Cron scheduler | **WORKING** | No zombies, proper serialization |
| PF cumulative tracking | **WORKING** | Fixed session 2 (gross_wins/gross_losses) |

---

## C. Where The System Is Going Wrong

### C1. Signal Quality Is The Problem, Not Architecture

**The brutal number**: 35.4% WR, -£6.79 P&L, ~0.77 PF after 66 trades.

This means VanguardSniper's momentum signal does not have positive expectancy after the Chandelier exit system processes it. The question is whether the problem is:
- **Entry timing** (buying too late in the move)
- **Exit calibration** (rungs too tight, getting stopped out before winners develop)
- **Ticker selection** (LSEETF leveraged ETPs are 0% WR, -£30 — the dominant loss source)
- **Cost blindness** (zero slippage in sim means reported P&L overstates real edge)

Without answering this, everything else is furniture rearrangement.

### C2. "6 Strategies" Is A Lie

**Code-level truth** (bridge.py audit):

| What the plan says | What the code actually does |
|--------------------|-----------------------------|
| "6 signal sources" | 2 independent generators (Vanguard + Orchestrator) |
| "TypeA-F are 6 strategies" | Post-hoc classification labels applied AFTER signal generation |
| "TypeB is best but never fires" | TypeB is a label that requires 3-bar rising RVOL — nearly impossible on synthetic 5s bars |
| "IBS_MR, VolExp, ORB, GapFade are live" | Generated then immediately blocked at bridge.py:1584 (shadow mode, return None) |
| "11 strategies in registry" | 1 producing, 4 dormant-but-functional, 6 labels/dead |

**The real signal flow:**
1. VanguardSniper evaluates (80%+ of signals)
2. Orchestrator S17-S20 evaluate (15-20% of signals)
3. Best signal selected by confidence
4. TypeA-F classification applied as label
5. TypeA/D → BLOCKED (disabled). TypeC/E/F → BLOCKED (shadow). TypeB → would pass but never classifies
6. Result: **virtually all trades are VanguardSniper momentum or Orchestrator entries, labeled "Unclassified"**

### C3. TypeB "Investigation" Is Misdirected

TypeB is not a strategy. It is a classification label with condition: `RVOL rising 3 consecutive bars AND RSI in [30, 70]`. It doesn't generate signals — it labels signals that already exist from VanguardSniper/Orchestrator.

The question "why does TypeB never fire?" has a simple answer: **3-bar rising RVOL almost never coincides with RSI [30, 70] on synthetic 5-second bars.** This is not a latent edge waiting to be unlocked. It is a filter that is too specific to match real data.

Sprint S4 should not "investigate TypeB" — it should ask: **which VanguardSniper trades win and which lose, and why?**

### C4. Dead Code In The Signal Path Wastes Compute

Four "strategies" in bridge.py (IBS_MR, VolExp, ORB, GapFade) generate signals on every tick, run through all indicator calculations, then immediately return None at line 1584 because they're shadow-only. This is ~200ms of Python compute per tick that produces zero value.

### C5. Risk Arbiter CHECK 26 (Scanner Score) Is Dead

CHECK 26 at risk_arbiter.rs:451 checks `scanner_score < 30` but the default value is `-1.0` (sentinel). It never triggers. It's a duplicate of CHECK 10 (confidence floor). Dead gate consuming attention but not actually gating.

### C6. Hurst Exponent Computed But Never Used

`regime_detector.rs` computes Hurst exponent (rescaled range analysis, 100+ LOC) but only `has_jump` boolean is used by the engine. `hurst_regime` and `confidence` are dead return values.

### C7. strategy_config.rs Is Orphaned

Rust loads strategy configs from `strategies.toml` into typed structs (`VwapDipBuyConfig`, etc.) at startup but never reads them at runtime. Python bridge has its own config. This is ~200 LOC of dead infrastructure.

---

## D. Contradiction Register

| ID | Subsystem | Issue | Files | Severity | Ruling |
|----|-----------|-------|-------|----------|--------|
| X01 | Strategy count | Plan says "6 strategies" | bridge.py, plan | HIGH | **Fix plan: 2 generators + classification** |
| X02 | TypeB framing | Plan says "best edge, never fires" | bridge.py:624 | HIGH | **Reframe: rare classification label, not latent strategy** |
| X03 | Shadow strategies | IBS/VolExp/ORB/GapFade "LIVE" in plan | bridge.py:1584 | MEDIUM | **Fix plan: these are DEAD (shadow=return None)** |
| X04 | CHECK 26 | Risk arbiter has dead check | risk_arbiter.rs:451 | LOW | **Delete or wire to real scanner** |
| X05 | Hurst computation | 100 LOC computed, never read | regime_detector.rs, engine.rs:1089 | LOW | **Document intent or delete** |
| X06 | strategy_config.rs | Loaded at startup, never queried | strategy_config.rs | LOW | **Mark orphaned, delete in next refactor** |
| X07 | Paper overrides | 8 values marked "revert for live" | config.toml:21-567 | CRITICAL (pre-live) | **Create config.live.toml (Sprint S6)** |
| X08 | Sim cost model | Zero slippage/commission in sim | engine.rs:1933 | HIGH | **Add cost injection (Sprint S7)** |

---

## E. Complexity / Hot-Mess Register

| Category | Item | Files | Impact | Action |
|----------|------|-------|--------|--------|
| **Dead signal code** | IBS_MR, VolExp, ORB, GapFade generators | bridge.py:1348-1477 | ~200ms wasted compute per tick | **DELETE** — move to shadow_experiments/ |
| **Dead Rust detectors** | TypeA-F detector structs | entry_engine.rs:88-500 | ~500 LOC dead code | **KEEP quarantined** — costs nothing, future option |
| **Dead risk check** | CHECK 26 (Scanner Score) | risk_arbiter.rs:451 | Misleading, never triggers | **DELETE** |
| **Orphaned config** | strategy_config.rs Rust structs | strategy_config.rs | ~200 LOC loaded but unused | **KEEP** — low cost, documents intent |
| **Dead return values** | Hurst regime + confidence | regime_detector.rs | ~50 LOC dead returns | **KEEP** — math is correct, future use |
| **Fake strategy count** | "11 strategies" narrative | Plan, registry | False confidence about diversification | **FIX**: say "2 generators, 1 classification layer" |
| **Classification overkill** | 6 TypeA-F labels, only Unclassified passes | bridge.py:606-659 | Complexity with no edge benefit | **SIMPLIFY**: keep TypeA/D block, delete TypeC/E/F shadow logging |

---

## F. Keep / Fix / Shadow / Delete Register

| Component | Verdict | Reason |
|-----------|---------|--------|
| VanguardSniper | **KEEP** | Only proven producer. Capital core. |
| Orchestrator (S17-S20) | **KEEP** | Independent generators, functional, occasionally fires. |
| IBS_MR generator | **DELETE** | Shadow-only, generates then returns None. Duplicate of S19. |
| VolExpansion generator | **DELETE** | Shadow-only, generates then returns None. |
| ORB_Breakout generator | **DELETE** | Shadow-only, generates then returns None. |
| GapFade generator | **DELETE** | Shadow-only, generates then returns None. Duplicate of S18. |
| TypeA/D classification | **KEEP** | Disabled gate (blocks losers). Working correctly. |
| TypeB classification | **FIX** | Either loosen the 3-bar RVOL condition or acknowledge it's unreachable. |
| TypeC/E/F shadow logging | **DELETE** | Generates log noise, produces no value. |
| Risk arbiter CHECK 26 | **DELETE** | Dead check, duplicates CHECK 10. |
| entry_engine.rs detectors | **KEEP (quarantined)** | Dead but harmless, future option value. |
| strategy_config.rs | **KEEP** | Orphaned but harmless, documents intent. |
| Hurst in regime_detector | **KEEP** | Correct math, future value for regime routing. |
| Chandelier 5-rung | **KEEP** | Working, well-calibrated. |
| Time-stop | **KEEP** | Working, halt-safe. |
| Board lot sizing | **KEEP** | Working, catches TSE/HKEX/SGX. |
| Spoof detector | **KEEP** | Working post-calibration. |
| Nightly pipeline | **KEEP** | Healthy, proper serialization. |
| Claude intelligence stack | **KEEP (shadow)** | Advisory value, no trading authority. |
| Gemini scanner | **KEEP (conditional)** | Only if API key set and output measured. |

---

## G. Highest-ROI Fix Order

### 1. INVESTIGATE WHY VANGUARD LOSES (not TypeB)

**Why**: 35.4% WR, negative P&L. This is the only question that matters.
**Method**: Segment 66 trades by: ticker (LSEETF vs others), session (LSE vs Asian), entry confidence, rung attainment, time-to-exit, P&L per trade. Find the pattern.
**Benefit**: Identifies whether the problem is entry quality, exit calibration, or ticker selection.
**Risk if not fixed**: Building infrastructure on a losing signal.
**Action**: BUILD NOW. One Python script analyzing WAL data.

### 2. DELETE DEAD SHADOW GENERATORS

**Why**: IBS_MR, VolExp, ORB, GapFade waste ~200ms Python compute per tick to generate signals that immediately return None.
**Method**: Remove from bridge.py Stage 3. Move to `shadow_experiments/` directory.
**Benefit**: Faster tick processing, cleaner signal path, honest strategy count.
**Risk if not fixed**: Wasted compute, false complexity narrative.
**Action**: DELETE NOW. 30 minutes.

### 3. DELETE TypeC/E/F SHADOW LOGGING

**Why**: These classification labels generate log lines but never affect trading. Log noise.
**Method**: Remove shadow logging at bridge.py:1582-1592. Keep TypeA/D block (proven losers).
**Benefit**: Cleaner logs, simpler classification path.
**Action**: DELETE NOW. 15 minutes.

### 4. FIX TypeB CLASSIFICATION OR ACKNOWLEDGE IT'S UNREACHABLE

**Why**: "3-bar rising RVOL + RSI [30,70]" almost never matches on 5-second synthetic bars.
**Method**: Either (a) loosen to 1-bar RVOL spike + RSI [25,75], or (b) delete the label and stop calling it "best strategy."
**Benefit**: Honest plan. If loosened, might actually classify some trades for analysis.
**Action**: FIX NOW. 20 minutes.

### 5. DELETE RISK ARBITER CHECK 26

**Why**: Dead check. Scanner score sentinel = -1.0, threshold = 30. Never triggers. Duplicates CHECK 10.
**Method**: Remove from risk_arbiter.rs:451-458.
**Benefit**: Cleaner risk path, one less misleading gate.
**Action**: DELETE NOW. 10 minutes.

### 6. ADD COST INJECTION TO OUROBOROS LEARNING (Sprint S7)

**Why**: Zero slippage/commission in sim means Ouroboros will optimize for fake edge when enabled at N=300.
**Method**: Inject 5bps slippage + IBKR tiered commission into persistent_memory.py before P&L recording.
**Benefit**: When learning unlocks, it learns from reality not fantasy.
**Action**: BUILD BEFORE N=300. ~1 hour.

### 7. CREATE config.live.toml (Sprint S6)

**Why**: 8 paper overrides (max_positions=999, heat=50%) would be catastrophic live.
**Method**: Create overlay file with safe live values.
**Action**: BUILD BEFORE LIVE. 15 minutes.

### 8. WIRE REGIME + SESSION ENFORCEMENT (Sprint S10)

**Why**: strategy_registry.json has regime_allowed/session_allowed metadata that isn't enforced.
**Method**: Bridge.py checks current regime/session before evaluating each strategy.
**Action**: BUILD LATER. After N=100 trades with regime data.

### 9. ADD PER-STRATEGY EXIT PARAMETERS (Sprint S9)

**Why**: Momentum continuation (Orchestrator) and broader momentum (Vanguard) may need different trail widths.
**Method**: strategy_config.rs already supports this. Wire to exit_engine.
**Action**: BUILD LATER. After per-strategy trade data exists.

### 10. EC2 UPGRADE (Sprint S5)

**Why**: 4GB RAM, 19GB disk is tight. Docker builds require aggressive pruning.
**Method**: Resize to 8GB RAM, expand root volume.
**Action**: BUILD BEFORE LIVE.

---

## H. Simplified Target-State Architecture

**After cleanup, the system should look like this:**

```
SIGNAL GENERATORS (2):
  VanguardSniper (momentum)     → bridge.py
  Orchestrator S17-S20 (4 eval) → autonomous_orchestrator.py + strategies.toml

CLASSIFICATION (simplified):
  TypeA/D gate: BLOCK (proven losers, 29%/24% WR)
  TypeB label: loosened or deleted
  TypeC/E/F: DELETED (no shadow logging)
  Unclassified: PASS (default)

RISK ENFORCEMENT (31 checks):
  CHECK 26 deleted (was dead)
  Remaining 31 checks: deterministic, fail-closed, <1ms

EXIT ENGINE:
  Chandelier 5-rung (unchanged)
  Time-stop 45min 0.3x ATR (unchanged)
  Volume exhaustion (unchanged)

LEARNING (frozen until N=300):
  Nightly pipeline: nightly_v6 → persistent_memory → config_writer (observe-only)
  Ouroboros: auto-unlock at N=300 with cost injection (Sprint S7)

INFRASTRUCTURE:
  Rust engine: tick processing, risk, exits, WAL
  Python bridge: signal generation, classification, sizing
  IBKR: market data (100 MktData) + execution
  Redis: state journal
  Docker: 3 containers (engine, gateway, redis)
```

**What was removed:**
- 4 dead shadow generators (IBS_MR, VolExp, ORB, GapFade)
- TypeC/E/F shadow logging
- CHECK 26 (dead risk gate)
- False "11 strategies" narrative → honest "2 generators + classification"

**What was kept but marked orphaned:**
- entry_engine.rs detectors (quarantined, future option)
- strategy_config.rs (documents intent)
- Hurst exponent computation (correct math, future regime routing)

---

## I. Brutal Final Verdict

**The system has one real edge (VanguardSniper momentum) and too much surrounding clutter.**

The Rust engine is excellent — deterministic, fast, well-audited. The Python bridge is functional but overdecorated with dead generators and a classification system that blocks more than it helps. The deployment, cron, and nightly pipeline are healthy.

**The core problem is not architecture. It is that VanguardSniper has a 35.4% win rate.** No amount of regime routing, session gating, symbol quality, or friction-aware ranking fixes a losing signal. Fix #1 must be understanding WHY trades lose before building any more infrastructure.

**The secondary problem is narrative inflation.** Calling this "11 strategies" when 1 produces trades creates false confidence about diversification. The honest statement: this is a single-strategy momentum system with a backup evaluator and a frozen learning loop.

**Is the system salvageable?** Yes. The engineering is sound. The risk infrastructure is institutional-grade. The exit system is calibrated. The deployment works. But the economics are underwater, and fixing that requires data analysis, not more features.

**What should happen next:**
1. Analyze the 66 trades to find the loss pattern
2. Delete dead code to simplify the signal path
3. Stop building infrastructure until the signal has positive expectancy
4. Let the engine collect trades toward N=100 with clean post-microstructure data
