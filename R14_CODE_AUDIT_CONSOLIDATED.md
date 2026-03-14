# AEGIS Master Plan v13.11 — Round 14 Code Audit Consolidated Report

**Auditor**: Claude Opus 4.6 (All 4 Personas)
**Date**: 2026-03-05
**Scope**: Deep forensic code audit of 5 critical modules + cross-reference against v13.11 spec
**Method**: 4 parallel audit agents, each reading actual Python code line-by-line

---

## EXECUTIVE SUMMARY

Round 14 performed a forensic code audit of the 5 most critical Python modules, verifying every R11-R13 finding against actual implementation.

**Result**: 4 P0 bugs CONFIRMED still present in code. 2 modules PASSED audit. 1 new finding discovered.

| Module | Status | Critical Findings |
|--------|--------|-------------------|
| `core/trading_discipline.py` | PASSED | All 7 gates present, all thresholds match spec, fully wired (3 entry points) |
| `qualification/dynamic_sizer.py` | 2 P0 CONFIRMED | GPT-54 (__setattr__ missing), GPT-61 (SHOCK_RECOVERY counts signals not sessions) |
| `feeds/regime_classifier.py` | 4 CRITICAL ISSUES | decrement_transition_buffer() orphaned, VIX hysteresis missing, flapping protection missing, SHOCK threshold VIX>45 not >40 |
| `core/ml_meta_model.py` | 1 P0 CONFIRMED | GPT-58 (_REGIME_MAP always returns -1 for actual regime states) |
| Signal queue (main.py) | 3 P0 CONFIRMED | GPT-12 (no consumer), GPT-55 (wrong exception class), GPT-39 (zero staleness) |

---

## PART I: MODULE-BY-MODULE FINDINGS

### 1. Trading Discipline Engine — PASSED

**File**: `core/trading_discipline.py` (437 lines)
**Verdict**: All 7 discipline gates (D-1 through D-7) present and operational.

| Check | Expected | Actual | Line | Status |
|-------|----------|--------|------|--------|
| MIN_SETUP_QUALITY | 65 | 65 | 43 | PASS |
| MAX_TRADES_PER_DAY | 4 | 4 | 63 | PASS |
| MAX_CONSECUTIVE_LOSSES | 4 | 4 | 54 | PASS |
| MIN_EDGE_EXPECTANCY | 0.10R | 0.10 | 47 | PASS |
| MAX_DAILY_LOSS_PCT | 3.0% | 3.0 | 60 | PASS |
| LOSS_COOLDOWN_MINUTES | 120 | 120 | 57 | PASS |
| SHOCK regime gate | Block on SHOCK | regime=="SHOCK" | 202 | PASS |
| VIX extreme gate | Block on VIX>35 | vix>35 | 210 | PASS |
| Quality floor | 50 | 50 | 71 | PASS |
| Pipeline wiring | 3 entry points | main.py:2879,3774,4266 | — | PASS |

**Zero orphaned code. Zero contradictions. Fully wired into all 3 signal paths.**

---

### 2. DynamicSizer — 2 P0 BUGS CONFIRMED

**File**: `qualification/dynamic_sizer.py` (1,486 lines)

#### P0 BUG #1: SHOCK_RECOVERY Counts Signals Not Sessions (GPT-61 CONFIRMED)
- **Location**: Lines 528-532, 342-349
- **Evidence**: `calculate_position_size()` is called for every candidate signal. Counter `_shock_recovery_remaining` decrements on EVERY call. If 5 signals evaluated per scan, "3-session recovery" completes in 18 seconds.
- **Line 92**: `_SHOCK_RECOVERY_SESSIONS = 3` — intended to be sessions, actually counts individual calls
- **Impact**: Post-SHOCK recovery period is effectively bypassed. System returns to full 0.75% sizing within 1 scan cycle instead of 3 sessions.

#### P0 BUG #2: ImmutableRiskRules __setattr__ Guard Missing (GPT-54 CONFIRMED)
- **Location**: `qualification/risk_sizer.py` lines 30-59
- **Evidence**: `_rules_locked = True` flag declared at line 59 but NEVER checked. No `__setattr__` override exists. `ImmutableRiskRules.RISK_PER_TRADE = 0.10` would silently succeed.
- **Impact**: Any code path (including learning engine) can modify risk limits at runtime.

#### P2: Documentation Claims 8 Factors, Actually 12
- **Location**: Lines 14-28 (docstring), 414-427 (composite calculation)
- **Evidence**: 8 core factors + 4 additional (confidence, time-of-day, correlation, shock recovery)
- **Impact**: Misleading documentation, no functional issue

#### P2: 5 Parameter Groups Hardcoded (Not Configurable via settings.yaml)
- CVaR thresholds (lines 131-134)
- Momentum crash VIX/scalar (lines 137-138)
- Confidence scaling (lines 106-109)
- Time-of-day windows (lines 97-103)
- Streak adjustment rules (lines 112-116)

---

### 3. Regime Classifier — 4 CRITICAL ISSUES

**File**: `feeds/regime_classifier.py` (340 lines)

#### CRITICAL #1: decrement_transition_buffer() Orphaned (GPT-56 CONFIRMED)
- **Location**: Lines 293-298 (definition), searched entire codebase — zero calls
- **Impact**: `in_transition` property (line 60-62) permanently returns True after first regime change, since buffer is set to 1 (line 185) but never decremented

#### CRITICAL #2: VIX Hysteresis Missing (GPT-46 NOT IMPLEMENTED)
- **Location**: Lines 127-141 — hard thresholds only, no deadband
- **Evidence**: SHOCK=VIX>45, RISK_OFF=VIX>35 — flat threshold, no proportional 15% band
- **Impact**: VIX oscillating around 35 toggles RISK_OFF/normal every 60 seconds

#### CRITICAL #3: Regime Flapping Protection Missing (GPT-80 NOT IMPLEMENTED)
- **Location**: Entire file searched — no rapid-change detection logic exists
- **Impact**: 10+ regime changes per minute possible during volatile boundary conditions

#### MEDIUM: SHOCK Threshold Discrepancy
- **Plan spec**: VIX > 40 triggers SHOCK
- **Code**: VIX > 45 triggers SHOCK (line 128)
- **Impact**: At VIX 40-45, system classifies as RISK_OFF instead of SHOCK

#### NOTE: HMM Uses 2 States (Not 3)
- **Location**: `regime_hmm.py:300-301` — `n_components=2`
- **Mapping**: 2 HMM states expanded to 5 output regimes via probability thresholds
- **Impact**: Plan says "3 HMM states" — needs clarification

---

### 4. ML Meta-Model _REGIME_MAP — P0 CONFIRMED (GPT-58)

**File**: `core/ml_meta_model.py` line 48

```python
_REGIME_MAP = {"bull": 0, "bear": 1, "neutral": 2, "volatile": 3,
               "trending": 4, "ranging": 5, "expansion": 6, "contraction": 7}
```

**Problem**: Actual RegimeState enum values are: `TRENDING_UP_STRONG`, `TRENDING_UP_MOD`, `TRENDING_DOWN_STRONG`, `TRENDING_DOWN_MOD`, `RANGE_BOUND`, `HIGH_VOLATILITY`, `RISK_OFF`, `SHOCK`

None of these match the _REGIME_MAP keys. `_encode_regime()` (line 118) returns -1 for ALL actual regime strings. The ML meta-model's regime feature is permanently broken — it always encodes -1.

---

### 5. Signal Queue — 3 P0 BUGS CONFIRMED

**Locations**: `main.py` lines 1136, 3074, 4201, 4430 + `tick_loop.py` line 1489

#### P0 BUG #1: No Consumer (GPT-12 CONFIRMED)
- Queue initialized at line 1136: `Queue(maxsize=50)`
- 4 write locations found, ZERO `.get()` calls in entire codebase
- All queued signals are dead data

#### P0 BUG #2: Wrong Exception Class (GPT-55 CONFIRMED)
- Queue from `queue` module (line 23: `from queue import Queue`)
- Exception caught: `asyncio.QueueFull` (lines 3081, 4208, 4437, tick_loop.py:1492)
- Should catch: `queue.Full`
- **Impact**: When queue fills to 50, `queue.Full` propagates uncaught → crash

#### P0 BUG #3: Zero Staleness Enforcement (GPT-39 PARTIALLY CONFIRMED)
- No `generated_at` timestamp on main.py queue writes
- No `signal_market_age` field
- No `max_signal_age` validation
- Only tick_loop.py parasite re-entry (line 1489) adds timestamp

---

## PART II: CROSS-REFERENCE VERIFICATION

### Prior Round Findings Now Code-Verified

| Amendment | Finding | R14 Verification |
|-----------|---------|------------------|
| GPT-54 | ImmutableRiskRules __setattr__ missing | CONFIRMED in risk_sizer.py:30-59 |
| GPT-55 | Wrong exception class (asyncio vs queue) | CONFIRMED in main.py:3081,4208,4437 + tick_loop.py:1492 |
| GPT-56 | decrement_transition_buffer() orphaned | CONFIRMED in regime_classifier.py:293 — zero calls |
| GPT-57 | S15/S16 bypass sanity gates | NOT RE-VERIFIED (separate audit needed) |
| GPT-58 | _REGIME_MAP returns -1 | CONFIRMED in ml_meta_model.py:48,118 |
| GPT-59 | SHAP feature dimension mismatch | NOT RE-VERIFIED (separate audit needed) |
| GPT-60 | yfinance inside VirtualTrader lock | NOT RE-VERIFIED (separate audit needed) |
| GPT-61 | SHOCK_RECOVERY counts signals | CONFIRMED in dynamic_sizer.py:528-532 |
| GPT-75 | TradingDisciplineEngine not in plan | NOW IN PLAN (§6B) — engine IS wired |
| GPT-80 | Regime flapping protection | CONFIRMED MISSING in regime_classifier.py |
| GPT-46 | VIX hysteresis | CONFIRMED MISSING in regime_classifier.py |

### Findings That Turned Out BETTER Than Expected

| Amendment | Original Fear | R14 Reality |
|-----------|---------------|-------------|
| GPT-75 | TradingDisciplineEngine orphaned | FULLY WIRED at 3 entry points (main.py:2879,3774,4266) |
| GPT-75 | 7 discipline gates missing | ALL 7 present, correct order, correct thresholds |
| GPT-75 | Quality floor missing | Present at 50 (line 71), drought decay works correctly |

---

## PART III: NEW FINDING FROM R14 (Not Previously Identified)

### GPT-100: VIX Default Fallback Permissiveness

**Discovery**: When `_current_market_ctx` is None (market context not yet populated), the TradingDisciplineEngine receives:
- `vix = 0.0` (permissive — bypasses D-7 VIX gate)
- `regime = "NEUTRAL"` (permissive — bypasses D-6 SHOCK gate)

This means during the first few scan cycles after startup (before market context is fetched), the discipline gates that should block trading in extreme conditions are **silently bypassed**.

**Severity**: P1
**Fix**: Default to `vix = 100.0` and `regime = "SHOCK"` when context is None (fail-CLOSED, not fail-OPEN). Or: block all trading until market context is populated (Startup Readiness Gate handles this if implemented).

---

## PART IV: STOP-SHIP STATUS

### Items Requiring Code Fix Before Live Trading

| # | Bug | Module | Lines | Fix Complexity |
|---|-----|--------|-------|----------------|
| 1 | Signal queue no consumer | main.py | 1136 | 6-8h (architecture) |
| 2 | Signal queue wrong exception | main.py + tick_loop.py | 3081,4208,4437,1492 | 0.5h (4 line changes) |
| 3 | ImmutableRiskRules mutable | risk_sizer.py | 30-59 | 1h |
| 4 | _REGIME_MAP broken | ml_meta_model.py | 48 | 1h |
| 5 | SHOCK_RECOVERY counts signals | dynamic_sizer.py | 528-532 | 2h |
| 6 | Transition buffer orphaned | regime_classifier.py | 293 | 1h |
| 7 | VIX hysteresis missing | regime_classifier.py | 127-141 | 2h |
| 8 | Regime flapping missing | regime_classifier.py | new | 2h |

**Minimum fix time for stop-ship items**: 16.5 hours

---

## SIGN-OFF

Round 14 code audit confirms 8 stop-ship bugs from R11-R13 are still present in code. The Trading Discipline Engine is the bright spot — fully wired, all thresholds correct, zero bugs. The most dangerous bugs are in the signal queue (dead-end + wrong exception = potential crash) and ImmutableRiskRules (constitutional risk limits are mutable).

**Auditor**: Claude Opus 4.6
**Date**: 2026-03-05
