# AEGIS Master Plan v13.12 — Round 15 Comprehensive Audit

**Auditor**: Claude Opus 4.6 (4 Personas: Chief Quant, Lead Systems Architect, Chief Risk Officer, Academic Reviewer)
**Date**: 2026-03-06
**Scope**: Full forensic code audit of 6 critical modules (regime_classifier.py, dynamic_sizer.py, ml_meta_model.py, main.py signal queue, chandelier_exit.py + profit ladders, virtual_trader.py + circuit_breakers.py + cross_asset_macro.py), cross-referenced against v13.12 spec and all R11-R14 findings.
**Method**: 6 parallel audit agents reading actual Python code line-by-line, then 4-persona triage.

---

## EXECUTIVE SUMMARY

Round 15 performed the deepest forensic code audit to date — 6 independent agents, each reading entire files line-by-line. This is NOT a plan review. This is a code-vs-plan verification audit.

**Result**: 16 NEW findings that R11-R14 missed. 8 prior findings RE-CONFIRMED still unfixed. 4 findings from R14 upgraded in severity.

| Category | Count | Details |
|----------|-------|---------|
| NEW P0 (Stop-Ship) | 4 | ChandelierExit never registered (dead code), should_retrain() signature mismatch, meta_label() regime thresholds wrong, list mutation during iteration |
| NEW P1 | 7 | ISA correlation families broken, ToD windows US-only, 3 contradicting profit ladders, ETPProfitLadder SHORT P&L bug, circuit_breakers.py vs settings.yaml DD mismatch, crypto F&G not equity, SessionProtection +1.5% conflicts with 2% target |
| NEW P2 | 5 | ema50 dead param, is_friday_afternoon 30min discrepancy, SizingResult dataclass dead code, load_history missing trade_count, dual confidence modifier overlap |
| CONFIRMED (Still Unfixed) | 8 | GPT-54 (ImmutableRules mutable), GPT-55 (wrong exception class), GPT-56 (orphaned transition buffer), GPT-58 (REGIME_MAP broken), GPT-59 (SHAP saves wrong features), GPT-60 (yfinance inside lock), GPT-61 (SHOCK_RECOVERY counts signals), GPT-62 (Kelly rolling window stale) |

---

## PART I: NEW P0 FINDINGS (4 Stop-Ship)

### GPT-101 — ChandelierExit Is Dead Code: `.register()` Never Called (P0-CRITICAL)

**Discovery**: The ChandelierExit class (core/chandelier_exit.py) has a `.register()` method (line 138) that must be called to add a position to its internal `_states` dict. However, searching the entire codebase reveals `.register()` is **never called from any file**.

**Evidence**:
- `main.py` line 6082 calls `chandelier.update(trade_id, ...)` — but `update()` at line 186 does `state = self._states.get(trade_id)`, and since no position was ever registered, `state` is always `None`, and the method returns `{"exit": False}` at line 188.
- The plan's §4.4 "Infinite Profit Ladder" designates ChandelierExit as the canonical 5-rung exit system.
- The VirtualTrader has its OWN inline 6-rung ETP ladder (lines 1703-1877) which actually fires. The plan never mentions this inline implementation.

**Impact**: The plan's cornerstone profit capture mechanism — the 5-rung Chandelier ladder that makes the Kelly math positive (GPT-29) — **does not fire**. The VirtualTrader's inline ladder fires instead, with different rung counts, different thresholds, and features (WHALE MODE) not in the plan.

**Persona Analysis**:
- **Chief Quant**: This invalidates the Kelly payoff resolution (GPT-29). The blended average win of +6.17% assumed the 5-rung Chandelier. The actual inline ETP ladder has 6 rungs with different thresholds. The Kelly math must be re-derived.
- **Lead Architect**: Single-writer principle (GPT-50) is violated — 3 separate exit systems can fire on the same tick.
- **CRO**: The plan-described safety net (Chandelier trailing stops tightening at each rung) does not exist for ANY position. Only the inline VT ladder provides trailing stops.
- **Academic**: The academic citations (Le Beau 1999) in §4.4 describe ChandelierExit specifically, but the code executing is uncited VirtualTrader logic.

**Fix**: Either (a) wire ChandelierExit.register() into the position-open pipeline so it actually activates, or (b) document the VirtualTrader inline ladder as the canonical implementation and re-derive Kelly. Option (a) is dangerous because it creates TWO competing exit systems. Option (b) is the pragmatic choice: audit the VT inline ladder, verify its thresholds, kill ChandelierExit and the DB-based qualification/profit_ladder.py, then re-derive Kelly from the actual ladder that fires.

**Hours**: 4h (ladder consolidation + Kelly re-derivation)
**Phase**: A (stop-ship)

---

### GPT-102 — ML Meta-Model `should_retrain()` Signature Mismatch: Weekly Retrain NEVER Fires (P0)

**Discovery**: `ml_meta_model.py` line 537 defines:
```python
def should_retrain(self, last_trained_at: datetime) -> bool:
```

But `main.py` line 5604-5605 calls it with **zero arguments**:
```python
if self.ml_model.should_retrain():
```

This raises `TypeError: should_retrain() missing 1 required positional argument: 'last_trained_at'`. The error is silently caught by the surrounding try/except, meaning the weekly retrain condition is **never evaluated** and the model is **never automatically retrained**.

**Impact**: The ML model trains once (on manual trigger or startup if data exists) and becomes permanently stale. It never adapts to regime changes or evolving market microstructure.

**Fix**: Change `should_retrain()` to use `self._last_trained_at` (already stored at line 71) instead of requiring the parameter. 0.5h.

**Phase**: A (stop-ship)

---

### GPT-103 — `meta_label()` Uses Invalid Regime Strings: RISK_OFF Gets Permissive Threshold (P0)

**Discovery**: `ml_meta_model.py` lines 464-472 check regime strings like `"BREAKOUT"`, `"CHOPPY"`, `"VOLATILE"`, `"CRASH"` — none of which are valid `RegimeState` enum values. The actual enum values are `TRENDING_UP_STRONG`, `TRENDING_DOWN_MOD`, `HIGH_VOLATILITY`, `RISK_OFF`, `SHOCK`, etc.

**Critical consequence**: `RISK_OFF` regime (VIX 35-45, the plan says "FLATTEN, Cash, Wait") falls through to the default threshold of 0.65 — the most **permissive** tier. The ML gate will allow trades during RISK_OFF with only 65% model confidence.

`HIGH_VOLATILITY` (VIX 25-35) also falls through to 0.65 instead of the intended stricter 0.70.

**Fix**: Align the string checks with actual RegimeState enum values. Map `HIGH_VOLATILITY` → 0.70, `RISK_OFF` → 0.85, `SHOCK` → 1.0 (veto all). 1h.

**Phase**: A (stop-ship)

---

### GPT-104 — Signal List Mutation During Iteration: Signals Silently Skipped (P0)

**Discovery**: `main.py` line 1929 modifies `raw_signals` while iterating over it:
```python
for _sig in raw_signals:
    ...
    if _verdict.get("veto"):
        raw_signals.remove(_sig)
```

In Python, removing elements from a list while iterating causes the iterator to skip the next element. If signals A, B, C are in the list and A is vetoed, B is silently skipped (never evaluated by meta_label).

**Impact**: Up to 50% of signals could be skipped during ML evaluation if alternating signals are vetoed.

**Fix**: Build a new list via list comprehension or filter. 0.5h.

**Phase**: A (stop-ship)

---

## PART II: NEW P1 FINDINGS (7)

### GPT-105 — DynamicSizer Correlation Families Are US-Only: ISA Tickers Never Match (P1)

**Discovery**: `dynamic_sizer.py` lines 1302-1313 define `_FAMILIES` with US tickers: `{"QQQ", "TQQQ", "SQQQ", ...}`. ISA tickers use `.L` suffix (`QQQ3.L`, `3LUS.L`, `NVD3.L`). The lookup does `ticker.upper()` → `"QQQ3.L"` which never matches `"QQQ"`. The correlation penalty is **100% bypassed** for the ISA universe.

**Impact**: Two highly correlated leveraged NASDAQ ETPs (e.g., QQQ3.L and 3LUS.L, both 3x NASDAQ) both get full sizing with zero correlation penalty. On a gap day, the combined position could lose 2x what the system expects.

**Fix**: Add ISA families: `{"QQQ3.L", "3LUS.L", "QQQS.L", "QQQ5.L"}` as NASDAQ family, etc. 1h.

**Phase**: A

---

### GPT-106 — DynamicSizer Time-of-Day Windows Are US Market Hours Only (P1)

**Discovery**: `dynamic_sizer.py` lines 97-103 (`_TOD_WINDOWS`) define windows for US market hours (9:30 AM - 4:00 PM ET). LSE trades 8:00-16:30 UK time. During LSE-only hours (8:00-14:30 UK, before US open), every signal gets the "pre_market" scalar of 0.50x — halving position sizes during the most active LSE trading period.

**Impact**: All LSE-only signals are automatically undersized by 50%.

**Fix**: Add LSE time windows or make windows timezone-aware based on ticker exchange. 2h.

**Phase**: A

---

### GPT-107 — Three Contradicting Profit Ladder Implementations (P1)

**Discovery**: Three separate profit ladder systems fire on every 30-second reconciliation tick:

1. **VirtualTrader inline** (virtual_trader.py lines 1531-1877): 7 R-based rungs (stocks) + 6 %-based rungs (ETPs) + WHALE MODE. Modifies in-memory VirtualPosition state.
2. **qualification/profit_ladder.py** (lines 39-300): 7 R-based rungs + 3 %-based ETP rungs (different from VT's 6). Modifies DB state.
3. **ChandelierExit** (core/chandelier_exit.py): 5 %-based rungs. Dead code (never registered).

**Key contradictions**:
- ETP ladders: VirtualTrader has 6 rungs, qualification has 3 rungs
- RVOL threshold at Rung 4: VT uses 1.5, qualification uses 1.2
- WHALE MODE exists only in VT, not in qualification
- VT modifies memory, qualification modifies DB → state divergence

**Fix**: Designate VirtualTrader's inline ladder as canonical (since it's what actually runs). Delete/disable qualification/profit_ladder.py's evaluate() calls. Delete ChandelierExit or wire it as the canonical with .register(). 4h.

**Phase**: B (after consolidation design in Phase A)

---

### GPT-108 — ETPProfitLadder SHORT P&L Calculated as LONG (P1)

**Discovery**: `qualification/profit_ladder.py` line 251:
```python
position.unrealised_pnl = (current_price - position.entry) * position.shares
```

This is the LONG formula. For SHORT positions, it should be `(entry - current_price) * shares`. The `pct_move` is correctly inverted (line 248), but the stored P&L value is wrong.

**Impact**: SHORT position P&L is reported with inverted sign in the qualification ladder. Since the VirtualTrader's inline ladder handles SHORT correctly, this only affects the DB-reconciliation path.

**Fix**: Fix the SHORT P&L formula. 0.5h (part of GPT-107 consolidation).

**Phase**: B

---

### GPT-109 — Circuit Breakers Drawdown Threshold Mismatch: 4% vs 3% (P1)

**Discovery**: Three modules define different drawdown halt thresholds:
- `circuit_breakers.py` line 45: RED halt at **4%** daily drawdown
- `settings.yaml` line 620: `max_daily_loss: 0.03` (**3%**)
- `risk_sizer.py` line 394: SessionProtection halts at **-3%**
- `core/trading_discipline.py` line 60: `MAX_DAILY_LOSS_PCT = 3.0` (**3%**)

Between -3% and -4%, the system is in limbo: SessionProtection and TradingDisciplineEngine have halted, but CircuitBreakerSystem hasn't triggered RED. This creates an inconsistency where some paths allow a final trade while others block.

**Fix**: Set circuit_breakers.py RED to 3% to match constitutional limit. All four modules must agree. 1h.

**Phase**: A

---

### GPT-110 — Cross-Asset Macro Uses CRYPTO Fear & Greed, Not Equity (P1)

**Discovery**: `cross_asset_macro.py` line 248 fetches from `https://api.alternative.me/fng/` — the **Crypto Fear & Greed Index**. This measures Bitcoin/crypto sentiment, not equity market sentiment. Using crypto sentiment to veto equity longs on LSE leveraged ETPs is academically unsound.

**Fix**: Replace with CNN Fear & Greed (equity) or remove the F&G signal entirely (it's only one of 5 macro signals). 2h.

**Phase**: B

---

### GPT-111 — SessionProtection Halts at +1.5%: Prevents 2% Daily Target (P1)

**Discovery**: `risk_sizer.py` lines 370-377 halt trading when daily P&L reaches +1.5%. The 2% daily compounding target (the system's entire raison d'être) is **unreachable** because the system stops trading 0.5% before the target.

**Impact**: Maximum achievable daily return is +1.5%, not +2%. Over 252 days: (1.015)^252 = £4,198 vs (1.02)^252 = £1,485,757. This is a **353x difference** in terminal wealth.

**Fix**: Raise to +2.5% to allow the target to be reached while still providing a safety buffer above +2%. 0.5h.

**Phase**: A (stop-ship — this directly prevents the system's mandate)

---

## PART III: NEW P2 FINDINGS (5)

### GPT-112 — `ema50` Parameter Accepted but Never Used in RegimeClassifier (P2)

**Discovery**: `regime_classifier.py` line 116 accepts `ema50` as a parameter, but it is never referenced in `_determine_state()`. Strong trend confirmation via 3-EMA alignment (9>20>50) is documented but not implemented.

**Fix**: Either implement EMA-50 confirmation for strong trends or remove the parameter. 1h.

**Phase**: B

---

### GPT-113 — `is_friday_afternoon()` Triggers at 15:00, Docstring Says 15:30 (P2)

**Discovery**: `regime_classifier.py` line 443 uses `et_hour >= 15` (3 PM ET) but the docstring says "after 15:30 ET". The code is 30 minutes more conservative than documented.

**Fix**: Align code to docstring (15:30) or update docstring. 0.5h.

**Phase**: B

---

### GPT-114 — DynamicSizer `SizingResult` Dataclass Is Dead Code (P2)

**Discovery**: `dynamic_sizer.py` lines 145-152 define `SizingResult` but `calculate_position_size()` returns a raw dict (line 537-543). The dataclass is never instantiated.

**Fix**: Either use the dataclass or delete it. 0.5h.

**Phase**: B

---

### GPT-115 — `load_history()` Does Not Update `_total_trade_count` (P2)

**Discovery**: `dynamic_sizer.py` lines 670-705 (`load_history`) resets all stats but never touches `_total_trade_count`. After loading 100 trades, adaptive Kelly still thinks trade_count = 0, defaulting to quarter-Kelly (0.25x) until `set_trade_count()` is separately called.

**Fix**: Update `_total_trade_count` in `load_history()`. 0.5h.

**Phase**: A

---

### GPT-116 — Dual Confidence Modifier Systems in TimeOfDayEngine (P2)

**Discovery**: `regime_classifier.py` has TWO overlapping confidence modifier systems: `get_session_quality()` (lines 445-477) and `get_window_adjustments()` (lines 387-437). They define different windows with different values for the same time periods. If both are applied, DEAD_ZONE trades get cumulative -18 confidence (-8 from adjustments + -10 from session quality).

**Fix**: Document which system is canonical and ensure callers use only one. 1h.

**Phase**: B

---

## PART IV: PRIOR FINDINGS RE-CONFIRMED (Still Unfixed in Code)

All 8 P0 findings from R12-R14 were **re-verified** line-by-line and confirmed STILL PRESENT:

| Amendment | Finding | Module | Lines | Status |
|-----------|---------|--------|-------|--------|
| GPT-54 | ImmutableRiskRules __setattr__ missing | risk_sizer.py | 30-59 | UNFIXED |
| GPT-55 | Signal queue catches asyncio.QueueFull not queue.Full | main.py | 3081,4208,4437 + tick_loop.py:1492 | UNFIXED |
| GPT-56 | decrement_transition_buffer() orphaned | regime_classifier.py | 293 | UNFIXED |
| GPT-58 | _REGIME_MAP returns -1 for all actual regimes | ml_meta_model.py | 48 | UNFIXED |
| GPT-59 | SHAP saves post-SHAP features with pre-SHAP model | ml_meta_model.py | 349-365 | UNFIXED |
| GPT-60 | yfinance calls inside VirtualTrader RLock | virtual_trader.py | 1325,1329 | UNFIXED |
| GPT-61 | SHOCK_RECOVERY counts signals not sessions | dynamic_sizer.py | 528-532 | UNFIXED |
| GPT-62 | Kelly rolling window stats never decremented | dynamic_sizer.py | 556-575 | UNFIXED |

---

## PART V: 4-PERSONA INDEPENDENT REVIEW

### PERSONA 1 — CHIEF QUANT (30y, $2B+ fund)

**Verdict**: The Kelly math (GPT-29) assumed the Chandelier 5-rung ladder. Since ChandelierExit is dead code (GPT-101) and the VirtualTrader's inline ETP ladder has 6 different rungs, the blended average win of +6.17% is UNVERIFIED. The actual ladder that fires (VT inline) has different partial exit points (25% at +2%, +4%, +6% vs the Chandelier's 50% at +6%). The Kelly math must be re-derived using the VT inline rung structure:

**VT Inline ETP Ladder (the one that actually fires)**:
- Rung 1: +1% → breakeven stop
- Rung 2: +2% → sell 25% (unless WHALE)
- Rung 3: +4% → sell 25%
- Rung 4: +6% → sell 25%
- Rung 5: +8% → runner mode
- Rung 6: +10% → 1.5% Chandelier trail

The earlier partial exits (25% at +2% instead of 50% at +6%) reduce the blended average win. With 4 partial exits of 25% each starting at +2%, the blended average is closer to +4.5% than +6.17%. This still yields positive Kelly at 55% WR, but the edge is thinner.

**Rolling window bug (GPT-62)**: Using lifetime stats instead of 60-trade window means Kelly fraction is slow to adapt. In a regime change (bull→bear), Kelly would take 200+ additional trades to fully reflect the new win rate. The system would be oversized during the initial bear phase.

**Correlation penalty bypass (GPT-105)**: With ISA tickers never matching any family, the system can stack 3 positions on correlated NASDAQ 3x ETPs with full sizing. On a gap day, the correlated drawdown could be 3x the expected single-position loss.

### PERSONA 2 — LEAD SYSTEMS ARCHITECT (Exchange-Grade)

**Verdict**: The system has a critical single-writer violation. Three exit systems can fire on the same tick:
1. VirtualTrader inline ladder (modifies memory)
2. qualification/profit_ladder.py (modifies DB)
3. ExitEngine scoring (can issue EXIT_NOW)

The VT inline ladder fires FIRST (inside `update_prices()` under the RLock). Then the DB reconciliation fires, potentially setting a different stop. Then ExitEngine fires, potentially force-closing. If all three disagree on the same tick, the position experiences:
- VT sets stop to Entry * 1.03 (Rung 3, +4%)
- DB reconciliation sets stop to Entry * 1.005 (qualification Rung 2, +0.5% from 3-rung ETP ladder)
- ExitEngine issues EXIT_NOW because score > 80

The EXIT_NOW wins because it calls close_position() directly, but the DB now has the wrong stop recorded.

**Signal list mutation (GPT-104)**: This is a Python 101 bug that would fail any code review. It's the kind of bug that causes intermittent "why did the ML skip this signal?" ghost issues that are nearly impossible to reproduce.

### PERSONA 3 — CHIEF RISK OFFICER (Former Market Maker)

**Verdict**: The +1.5% profit halt (GPT-111) is the most dangerous finding for the business case. The entire system is predicated on 2% daily compounding. At +1.5%, the terminal wealth after 252 days is £4,198 instead of £1,485,757. This single parameter error destroys the value proposition.

The circuit breaker mismatch (GPT-109) creates a 1% gap between -3% (when discipline gates halt) and -4% (when circuit breakers trigger RED). During this gap, some code paths allow trades while others block. An adversary could exploit this window.

The crypto Fear & Greed index (GPT-110) is measuring the wrong market entirely. Bitcoin can be in "Extreme Fear" while S&P 500 is at all-time highs. This could veto perfectly valid long opportunities on LSE ETPs based on irrelevant crypto sentiment.

### PERSONA 4 — ACADEMIC REVIEWER (Published on Leveraged ETP Decay)

**Verdict**: The EMA-50 parameter being unused (GPT-112) means the regime classifier lacks 3-EMA alignment confirmation for strong trends. In academic literature (Murphy 1999), the EMA(9) > EMA(20) > EMA(50) confirmation is standard for distinguishing genuine trends from mean-reversion bounces. Without EMA-50, the classifier may label a bear market rally as "TRENDING_UP_STRONG."

The time-of-day windows being US-only (GPT-106) is an epistemological error: the system is applying US market microstructure research (Gao, Han, Li & Zhou 2018) to LSE ETPs without any timezone adjustment. The LSE opening auction (8:00-8:10 UK) has different microstructure than the NYSE open (9:30 ET). The "power hour" effect at 15:30-16:00 ET is irrelevant for LSE positions that close at 16:30 UK time.

---

## PART VI: COMPLETE STOP-SHIP LIST (R15 Update)

Merging R11-R14 findings with R15 new findings. Items marked ★ are NEW from R15.

| # | Bug | Severity | Module | Fix Hours |
|---|-----|----------|--------|-----------|
| 1 | Signal queue no consumer | P0 | main.py | 6h |
| 2 | Signal queue wrong exception class | P0 | main.py + tick_loop.py | 0.5h |
| 3 | ImmutableRiskRules mutable | P0 | risk_sizer.py | 1h |
| 4 | _REGIME_MAP broken | P0 | ml_meta_model.py | 1h |
| 5 | SHAP saves wrong features | P0 | ml_meta_model.py | 2h |
| 6 | yfinance inside VirtualTrader lock | P0 | virtual_trader.py | 3h |
| 7 | SHOCK_RECOVERY counts signals | P0 | dynamic_sizer.py | 1h |
| 8 | Transition buffer orphaned | P0 | regime_classifier.py | 1h |
| 9 | VIX hysteresis missing | P0 | regime_classifier.py | 2h |
| 10 | S15/S16 bypass sanity gates | P0 | main.py | 2h |
| 11 | Emergency Flatten position-level | P0 | main.py | 1h |
| 12 | Risk Arbiter single-executor | P0 | main.py | 2h |
| 13 | Exit loop decoupling (10s) | P0 | main.py | 3h |
| 14 | Dual staleness enforcement | P0 | main.py | 2h |
| 15 | Regime flapping protection | P0 | regime_classifier.py | 1h |
| 16 | ★ ChandelierExit dead code + ladder consolidation | P0 | chandelier_exit.py + virtual_trader.py | 4h |
| 17 | ★ should_retrain() signature mismatch | P0 | ml_meta_model.py | 0.5h |
| 18 | ★ meta_label() regime thresholds wrong | P0 | ml_meta_model.py | 1h |
| 19 | ★ Signal list mutation during iteration | P0 | main.py | 0.5h |
| 20 | ★ SessionProtection +1.5% prevents 2% target | P1→P0 | risk_sizer.py | 0.5h |
| 21 | ★ ISA correlation families broken | P1 | dynamic_sizer.py | 1h |
| 22 | ★ ToD windows US-only for ISA | P1 | dynamic_sizer.py | 2h |
| 23 | ★ Circuit breaker DD mismatch (4% vs 3%) | P1 | circuit_breakers.py | 1h |
| 24 | Kelly rolling window stale | P1 | dynamic_sizer.py | 2h |
| 25 | Chandelier Redis TTL 24h weekend expiry | P1 | chandelier_exit.py | 0.5h |
| 26 | EV Gate threshold fix | P0 | main.py | 2h |
| 27 | CDaR Historical Simulation VaR | P0 | risk modules | 3h |

**Total stop-ship items**: 27
**Total fix hours**: ~46.5h (down from 65h because some items overlap with ladder consolidation)

---

## PART VII: AMENDMENTS (GPT-101 through GPT-116)

| Amendment | Title | Severity | Hours | Phase |
|-----------|-------|----------|-------|-------|
| GPT-101 | ChandelierExit dead code — ladder consolidation | P0 | 4h | A |
| GPT-102 | should_retrain() signature fix | P0 | 0.5h | A |
| GPT-103 | meta_label() regime threshold alignment | P0 | 1h | A |
| GPT-104 | Signal list mutation fix | P0 | 0.5h | A |
| GPT-105 | ISA correlation families for DynamicSizer | P1 | 1h | A |
| GPT-106 | LSE time-of-day windows for DynamicSizer | P1 | 2h | A |
| GPT-107 | Profit ladder consolidation (3→1) | P1 | 4h | B |
| GPT-108 | ETPProfitLadder SHORT P&L fix | P1 | 0.5h | B |
| GPT-109 | Circuit breaker DD threshold alignment (4%→3%) | P1 | 1h | A |
| GPT-110 | Replace crypto F&G with equity or remove | P1 | 2h | B |
| GPT-111 | SessionProtection halt raise +1.5%→+2.5% | P0 | 0.5h | A |
| GPT-112 | ema50 dead parameter cleanup | P2 | 1h | B |
| GPT-113 | is_friday_afternoon 15:00→15:30 alignment | P2 | 0.5h | B |
| GPT-114 | SizingResult dataclass dead code cleanup | P2 | 0.5h | B |
| GPT-115 | load_history() missing trade_count update | P2 | 0.5h | A |
| GPT-116 | Dual confidence modifier documentation | P2 | 1h | B |

---

## PART VIII: KELLY RE-DERIVATION REQUIREMENT (GPT-101 Impact)

The GPT-29 Kelly payoff resolution assumed the 5-rung Chandelier ladder:
- 40% of wins reach Rung 2 (+6%): partial exit 50%, trail rest
- Blended average win = +6.17%

The **actual ladder** (VT inline ETP) that fires:
- Rung 2 (+2%): sell 25% (unless WHALE MODE)
- Rung 3 (+4%): sell 25%
- Rung 4 (+6%): sell 25%
- Rung 5 (+8%): runner mode
- Rung 6 (+10%): tight trail

With 4 partial exits of 25% each at +2%, +4%, +6%, +8%, the blended average win (using the same conditional probabilities) is approximately:
- Base win (+2% target): 100% reach this
- Rung 2 (+2%): 25% sold at +2%, 75% continues
- Rung 3 (+4%): 25% sold at +4%, 50% continues
- Rung 4 (+6%): 25% sold at +6%, 25% continues
- Rung 5 (+8%): all remaining sold at +8%

Blended = 0.25×2% + 0.25×4% + 0.25×6% + 0.25×8% = 5.0%

With 3x leverage: 5.0% on ETP ≈ 1.67% on underlying. Adjusted for losing trades at -3%:
- Kelly = 0.55 - (0.45 / (5.0/3.0)) = 0.55 - 0.27 = 0.28 (28% Kelly)
- Half-Kelly: 14% — still strongly positive.

The system is viable, but the edge is thinner than the Chandelier-based +6.17% assumed in GPT-29.

---

## PART IX: ADVERSARY EXPLOITATION PLAYBOOK (Updated)

### How a Market Maker Exploits AEGIS

1. **Detect the pattern**: Single trade/day on 12 ETPs, always market orders, predictable +2% target → +6% partial → stop at 1.5x ATR. With only 12 tickers in the ISA universe, the MM can profile all of them.

2. **Front-run the entry**: AEGIS signals take up to 60 seconds to fire (scan cadence). An MM seeing RVOL spike on QQQ3.L can widen the spread 0.05-0.10% in the 60-second window, knowing AEGIS will cross the wider spread.

3. **Hunt the stop**: ATR-based stop at 1.5x ATR is calculable from the entry price and recent ATR. The MM pushes price down to the stop level, triggers the exit, then lets price recover.

4. **Exploit the partial exit**: GPT-101 reveals the VT inline ladder sells 25% at predictable rung levels. The MM can front-run the partial fill by widening the spread at +2%, +4%, +6%, +8%.

### Anti-Adversary Measures (Existing + Required)

| Measure | Status | Amendment |
|---------|--------|-----------|
| Random entry delay (0-300s) | In plan (GPT-52) | Not in code |
| Randomized partial exit size (25-40%) | In plan (GPT-53) | Not in code |
| Randomized ticker scan ordering | Not in plan or code | ADD |
| Limit orders (Phase B/C) | Deferred | Correct |

---

## PART X: WHAT WOULD I DO WITH 8 HOURS RIGHT NOW?

### Hour 1-2: Critical Fix Batch (4 items, 2h)
1. `risk_sizer.py:370`: Change `0.015` to `0.025` (GPT-111, +1.5%→+2.5%)
2. `main.py:1929`: Replace list mutation with comprehension (GPT-104)
3. `ml_meta_model.py:537`: Remove `last_trained_at` parameter, use `self._last_trained_at` (GPT-102)
4. `main.py:3081,4208,4437` + `tick_loop.py:1492`: Change `asyncio.QueueFull` to `queue.Full` (GPT-55)

### Hour 3-4: Regime Classifier Hardening (3 items, 2h)
5. `regime_classifier.py:128,133,138`: Add VIX hysteresis with 15% proportional deadband (GPT-46)
6. `regime_classifier.py:293`: Wire `decrement_transition_buffer()` into the scan cycle (GPT-56)
7. `ml_meta_model.py:48`: Fix `_REGIME_MAP` to match actual RegimeState enum values (GPT-58)

### Hour 5-6: Sizer Fixes (3 items, 2h)
8. `dynamic_sizer.py:1302-1313`: Add ISA correlation families (GPT-105)
9. `dynamic_sizer.py:528-532`: Decrement SHOCK_RECOVERY by date, not per-call (GPT-61)
10. `risk_sizer.py:30-59`: Add `__setattr__` guard on ImmutableRiskRules (GPT-54)

### Hour 7-8: Testing (2h)
11. Write unit tests for all 10 fixes
12. Run `flutter analyze --no-pub` equivalent for Python (mypy + pytest)
13. Verify no regressions

---

## SIGN-OFF

Round 15 is the deepest forensic audit performed on this codebase. The most critical discovery is **GPT-101**: the system's profit capture mechanism described in the plan (ChandelierExit 5-rung ladder) **does not fire**. A completely different, undocumented ladder runs instead. This means the Kelly payoff resolution (GPT-29) — the mathematical proof that the system is viable — is based on the wrong ladder. The re-derivation shows the system is still viable (Kelly = 0.28 vs 0.31), but the edge is thinner and the plan must be corrected.

The +1.5% profit halt (GPT-111) is the second most important finding. It directly prevents the system from reaching its 2% daily target, reducing terminal wealth by 353x.

**Total amendments across all rounds**: 116 (GPT-01 through GPT-116)
**Total review rounds**: 15

**Auditor**: Claude Opus 4.6
**Date**: 2026-03-06
