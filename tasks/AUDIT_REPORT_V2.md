# NZT-48/AEGIS FULL-SPECTRUM INSTITUTIONAL + ACADEMIC SYSTEM AUDIT — V2

**Date**: 2026-03-08
**Version**: V2 (Deep-Dive Corrected & Extended Edition)
**System**: NZT-48 Automated Leveraged ETP Trading System
**Plan**: AEGIS Master Plan v16.2 (2,414 lines, 98 stop-ship items)
**Codebase**: ~7,700 lines (main.py) + ~25 modules
**Deployment**: EC2 c7i-flex.large, Docker Compose (3 containers)
**Mode**: Paper trading, £10,000 starting equity, UK ISA
**Track Record**: 0 wins / 52 trades (0% win rate)

---

## SECTION A: EXECUTIVE VERDICT

### A-1. Verdict

**NOT READY FOR LIVE CAPITAL — RED STATUS**

### A-2. Rating

**RED — Critical structural defects across execution, risk, data, and pipeline architecture. Multiple disconnected systems. 0/8 self-learning components influence decisions.**

The system is an ambitious, well-researched, and extensively documented trading framework. It contains over 30 discrete subsystems covering nearly every institutional risk concern found in the academic literature. However, the engineering reality diverges from the plan in multiple critical ways. The system has never produced a single winning trade in 52 attempts. The root causes are architectural, not parametric.

**V2 CORRECTIONS TO V1**: V1 audit contained 5 material errors that understated system defects. V2 corrects these and adds 4 new Tier-1 findings.

### A-3. V1 CORRECTIONS

| V1 Finding | V1 Claim | Actual Reality | V2 Correction |
|------------|----------|---------------|---------------|
| #12 | ORANGE CB size_multiplier = 0.0 | `circuit_breakers.py:910` = 0.50 | **WRONG. ORANGE is 0.50, not 0.0.** |
| #13 | Fear & Greed = crypto (Alternative.me) | `cross_asset_macro.py:310` fetches CNN equities | **WRONG. Uses CNN equities, not crypto.** |
| #16 | Weekly halt = 6% (code vs plan) | BOTH 6% AND 8% exist in code | **INCOMPLETE. Two values coexist.** |
| C-02 | Confidence floors: "3 different values" | Actually 4+ values (55, 58, 60, 65, 75) | **UNDERSTATED. 5 values, not 3.** |
| C-03 | Max positions: "3 vs 4" | Actually 5 values (2, 3, 3, 4, 5) | **UNDERSTATED. 5 values, not 2.** |

### A-4. Critical Findings (Tier 1 — Each Is Independently Disqualifying)

**FINDING C-01: DISCONNECTED SIGNAL PIPELINES — THE #1 CRITICAL BUG**

There are 4 signal-generation paths and 1 execution path. The first 3 paths are COMPLETELY DISCONNECTED from execution:

| Path | Trigger | Destination | Opens Positions? |
|------|---------|-------------|------------------|
| **PATH 1**: S15 Priority Path | APScheduler 60s scan → `run_scan()` → 30+ gates | `_signal_queue.put_nowait()` | **NO** |
| **PATH 2**: S16 Medium Gauntlet | APScheduler 60s scan → S16 pipeline → 18+ gates | `_signal_queue.put_nowait()` | **NO** |
| **PATH 3**: General 18-Gate Pipeline | APScheduler 60s scan → full pipeline | `_signal_queue.put_nowait()` | **NO** |
| **PATH 4**: TickLoop Brain | TickLoop 30s → own SignalEngine → 6 gates → `virtual_trader.open_position()` | **YES** |

**Evidence**:
1. All three APScheduler paths push to signal queue at `main.py:4234`, `main.py:2432`, `main.py:3341`.
2. Signal queue consumer at `main.py:6829-6878` ONLY logs and pushes to API dashboard — it does NOT call `open_position()`.
3. The TickLoop has its OWN separate SignalEngine (`tick_loop.py:500-510`) that generates PlayScore objects independently.
4. The ONLY line that opens positions is `tick_loop.py:787`: `self._virtual_trader.open_position(signal)`.

**Impact**: All 30+ sophisticated gauntlet gates in S15/S16/General paths are WASTED. They generate signals that are logged, pushed to the dashboard, and never executed. The TickLoop uses 6 simpler gates (`tick_loop.py:742-787`) before calling `open_position()`. The APScheduler's 60-second scans are effectively theatre.

**FINDING C-02: 22 THRESHOLD CONTRADICTIONS (V1 found only 6)**

V2 deep-dive found 22 critical threshold contradictions across 20 threshold categories:

| ID | Threshold | Values in Code | Evidence |
|----|-----------|---------------|----------|
| **C-01** | Weekly loss halt | 6% vs 8% | `risk_sizer.py:52` = 6%, `circuit_breakers.py:120` = 8% |
| **C-02** | Max concurrent positions | 2, 3, 3, 4, 5 (FIVE) | `tick_loop.py:286`=2, `risk_sizer.py:53`=3, `threshold_registry.py`=3, `settings.yaml:623`=4, `universal_scanner.py`=5 |
| **C-03** | Confidence floor (general) | 55, 60, 65, 75 | `gate_diagnostics.py:62`=55, `main.py:2973`=60, `risk_sizer.py:57`=65, `risk_sizer.py:59`=75 (post-3-losses) |
| **C-04** | Confidence floor (S16) | 58 vs 65 | `universal_scanner.py:17`=58, `risk_sizer.py:57`=65 |
| **C-06** | Max signals per day | 1 vs 3 | `gate_diagnostics.py:61`=1, `daily_target.py:74`=3 |
| **C-08** | RVOL min (global lowest) | 0.40 | `signal_engine/gates.py:40`=0.40 (lowest anywhere, contradicts all others) |
| **C-09** | Daily loss limit | 3% vs 4% | `settings.yaml:621` max_daily_loss=3%, `circuit_breakers.py:84` L3=4% |
| **C-10** | Spread veto multiplier | 1.8x vs 2.5x | `daily_target.py:1088`=1.8x, `threshold_registry.py:105`=2.5x |
| **C-11** | Consecutive loss tier 2 | 4 vs 5 | `circuit_breakers.py:89`=4, `threshold_registry.py:102`=5 |
| **C-13** | Staleness limit | 120s vs 300s | `threshold_registry.py:112`=120s, `invariant_enforcer.py:115`=300s |
| **C-14** | Max concurrent (most restrictive) | 2 | `tick_loop.py:286`=2 (blocks all others) |
| **C-15** | Profit ladder bank_pct | Code: 0.0/0.15/0.33/0.50/0.0 vs Plan: all 0.33 | `chandelier_exit.py:46-50` vs `aegis/03_EXECUTION_TIMING.md:89` |
| **C-16** | Overnight kill scope | Code: 5x only. Plan: ALL ETPs | `virtual_trader.py:1501-1514` only checks 5x, 3x can hold overnight |
| **C-19** | ATR multiplier | 1.0 vs 1.5 | `gate_diagnostics.py:85`=1.0, `threshold_registry.py:106`=1.5 |
| **C-20** | Transition buffer decrement | Defined but NEVER CALLED | `regime_classifier.py:500` has function, 0 callers |

Additional contradictions in V2: RVOL thresholds (0.40/0.60/0.80), ADX thresholds (15/20/25), VIX hysteresis (5%/15%), session phase gates, Stoikov EV gates, correlation penalties.

**FINDING C-03: INVARIANT ENFORCER IS DEAD CODE**

The 12-invariant safety net documented in AEGIS Blood Oath #4 is completely non-functional:

1. **Never scheduled**: `_run_invariant_runtime_check()` defined at `main.py:1329` but NEVER scheduled. Searched all 66 `add_job()` calls — zero schedule the invariant checker.
2. **Never boot-checked**: `enforce_boot()` defined at `invariant_enforcer.py:68` but NEVER called in startup sequence.
3. **Silent failure on init**: `main.py:1295-1296` wraps InvariantEnforcer init in blanket `except` labeled "non-critical". If the 12-invariant enforcer fails to construct, the system proceeds as if healthy.
4. **All 12 invariants are dead**: RI-01 through RI-12 documented in AEGIS plan but none are checked at runtime.

**Evidence**: `grep -rn "enforce_boot" main.py` → 0 results. `grep -rn "_run_invariant_runtime_check" main.py` → 1 definition, 0 calls.

**FINDING C-04: OVERNIGHT KILL SCOPE VIOLATION**

The overnight kill logic at `virtual_trader.py:1501-1514` only enforces for 5x products:

```python
if any(x in self.ticker for x in ["QQQ5", "SP5L"]):  # 5x products only
    # Enforce overnight kill
```

**Impact**: 3x products (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L) can be held overnight in violation of the plan's mandate that ALL leveraged ETPs must be closed before 16:00.

**Evidence**: `virtual_trader.py:1501-1514`. Plan says "ALL ETPs" (`aegis/03_EXECUTION_TIMING.md:44`).

**FINDING C-05: DUAL KILL SWITCHES WITH DESYNC**

Two independent kill switch implementations:
1. **File-based** (`telegram_bot.py:1820-1863`): `data/KILL_SWITCH` file + in-memory `_process_killed` flag
2. **Redis-based** (`state_manager.py:317-331`): `nzt:kill` hash with no TTL

**Desync bug**: `KillSwitch.deactivate()` at `telegram_bot.py:1857-1863` ONLY clears the file, NOT Redis. Redis kill switch persists after deactivation.

**Evidence**: `KillSwitch.deactivate()` calls `os.remove(self._KILL_FILE)` but never calls `state_manager.clear_kill()`.

**FINDING C-06: THREE INDEPENDENT CONFIDENCE FLOORS WITH CONTRADICTORY AUTHORITY**

Five different confidence thresholds exist:
- `ImmutableRiskRules.MIN_CONFIDENCE = 65` (`risk_sizer.py:57`) — constitutional
- `ImmutableRiskRules.CONSECUTIVE_LOSSES_3_CONF = 75` (`risk_sizer.py:59`) — after 3 losses
- `gate_diagnostics.py:62` uses `_MIN_CONFIDENCE = 55.0`
- `main.py:2973` uses `min_confidence = 60.0`
- `universal_scanner.py:17` uses `_MIN_CONFIDENCE_S16 = 58.0`
- `daily_target.py:75` uses `_MIN_CONFIDENCE = 65.0`

**Impact**: Signals can be diagnosed as "passing" in gate diagnostics (55) while failing the constitutional check (65). S16 uses 58, which is 7 points below the constitutional floor.

**ThresholdRegistry bypass**: `ThresholdRegistry.confidence_floor = 65` (`threshold_registry.py:99`) exists as "Blood Oath #1" single source of truth, but ZERO production modules import from it. Every module hardcodes its own floor.

**FINDING C-07: FIVE DIFFERENT MAX POSITION VALUES**

| Location | Value | Evidence |
|----------|-------|----------|
| TickLoop | 2 | `tick_loop.py:286` `MAX_CONCURRENT = 2` |
| ImmutableRiskRules | 3 | `risk_sizer.py:53` `MAX_CONCURRENT_POSITIONS = 3` |
| ThresholdRegistry | 3 | `threshold_registry.py:97` `max_concurrent_positions = 3` |
| settings.yaml | 4 | `settings.yaml:623` `max_concurrent_positions: 4` |
| UniversalScanner | 5 | `universal_scanner.py:892` implicit from spread logic |

**Impact**: TickLoop's MAX_CONCURRENT=2 is the MOST RESTRICTIVE and blocks all other systems from opening more than 2 positions. The constitutional value of 3 is never achieved.

**FINDING C-08: ML META-MODEL DISABLED BUT STILL WIRED**

`_ML_ENABLED = False` at `ml_meta_model.py:56` (AEGIS 0-05). When False:
- `meta_label()` returns pass-through (`{veto: False, p_success: 0.5}`)
- `predict_proba()` returns 0.5
- `train()` is no-op

However:
1. System still constructs MLMetaModel object on every boot
2. All ML dependencies loaded (sklearn, shap, joblib)
3. If someone sets `_ML_ENABLED = True`, model trains on **43.7% fabricated data** (comment at line 52)
4. Broken regime encoding always returns -1
5. All learning components remain wired to dead models

**FINDING C-09: ZERO OF 8 SELF-LEARNING COMPONENTS CHANGE DECISIONS**

See Section E for full analysis. Summary:
- IncrementalLearner: `predict_proba()` NEVER called → ZERO influence
- DriftDetector: Reads phantom file `drift_report.json` (nothing writes it) → ZERO influence
- BayesianWinRate: Display only, not wired to Kelly or any gate → ZERO influence
- ActiveWeighter: Feeds dead models → ZERO influence
- EnsembleDiversity: `predict_ensemble()` NEVER called → ZERO influence
- LearningEngine: Only DecayDetector sub-module gates strategies → PARTIAL influence
- MLMetaModel: `_ML_ENABLED = False` → ZERO influence
- HMMRegimeOverlay: NEVER IMPORTED (orphaned; real HMM is `core/regime_hmm.py`) → ZERO influence

**FINDING C-10: 98 STOP-SHIP ITEMS, ZERO IMPLEMENTED**

The plan itself (AEGIS_COMPLETE.md) documents 98 items (40 P0 + 58 P1) that must be completed before live trading. The code has zero of these fully implemented and verified.

### A-5. Significant Findings (Tier 2 — Material Risk)

**FINDING S-01: SessionProtection halts at +2.0% daily P&L, which equals the daily target.**
The system's maximum theoretical daily gain is exactly its target — zero upside beyond 2.0%. After costs (3 trades × 15-25bps round-trip = 45-75bps), the maximum achievable daily return is ~1.25-1.55%.

Evidence: `risk_sizer.py:397`

**FINDING S-02: Redis uses global `noeviction` policy.**
DB 0 (critical state) requires noeviction — correct. DB 1 (telemetry) was designed for `allkeys-lru` but Redis 7 applies maxmemory-policy globally. When the 400MB limit is hit, ALL writes fail — including position state and circuit breaker persistence.

Evidence: `docker-compose.yml:98`

**FINDING S-03: TickLoop can fail silently at startup.**
TickLoop start (`main.py:9222-9225`) wrapped in try/except that catches ALL exceptions and logs a WARNING. If it fails, `self._tick_loop = None`, and the system continues to scan, generate signals, and push them to the dashboard, but zero positions are ever opened.

Evidence: `main.py:9222-9225`

**FINDING S-04: S16 Universal Scanner emits SHORT signals with no ISA long-only guard.**
S16 explicitly supports SHORT direction signals in gap scanner (`universal_scanner.py:473`), VWAP bounce (line 527), momentum breakout (line 579), RSI reversal (line 635), and sector rotation (line 692). S15 has proper ISA guard at `daily_target.py:1110-1116`. S16 has no equivalent. SHORT signals from S16 flow through and would attempt illegal short-selling in UK ISA.

Evidence: `universal_scanner.py:473,527,579,635,692`

**FINDING S-05: ImmutableRiskRules not actually immutable.**
Instance-level mutation blocked via `__setattr__` guard (`risk_sizer.py:37`). Class-level mutation UNPROTECTED (no metaclass). Any module can do `ImmutableRiskRules.RISK_PER_TRADE = 0.05` silently.

Evidence: `risk_sizer.py:31-69` — no metaclass `__setattr__` override.

**FINDING S-06: Transition buffer decrement defined but NEVER CALLED.**
`decrement_transition_buffer()` defined at `regime_classifier.py:500` but has ZERO callers. Regime flapping protection is non-functional.

Evidence: `grep -rn "decrement_transition_buffer"` → 1 definition, 0 calls.

**FINDING S-07: THREE profit ladder implementations.**
Three separate profit ladder systems exist:
1. `chandelier_exit.py` — Le Beau 1999, 5-rung ladder, Redis-persisted
2. `profit_ladder.py` — Alternative implementation
3. `virtual_trader.py:1666-1690` — Inline profit ladder logic

Last-writer-wins in `update_prices()` loop. Conflicting exit signals possible.

Evidence: `chandelier_exit.py`, `profit_ladder.py`, `virtual_trader.py:1666`

**FINDING S-08: ProfitLadder.evaluate() called with atr=0.0.**
`main.py:7118` calls `ProfitLadder.evaluate(atr=0.0)`. Trailing stops produce zero trail distance.

Evidence: `main.py:7118`

**FINDING S-09: ETPProfitLadder SHORT P&L sign bug.**
`ETPProfitLadder` at `profit_ladder.py:252` has SHORT P&L sign calculation bug (GPT-108).

Evidence: Comment at `profit_ladder.py:252`

**FINDING S-10: Correlation families use wrong tickers.**
`dynamic_sizer.py:1302-1313` defines correlation families with US tickers (SPY, QQQ, IWM). ISA `.L` tickers never match. Correlation penalty has NEVER fired in 52 trades.

Evidence: `dynamic_sizer.py:1302-1313`

**FINDING S-11: Stale data age not checked.**
TickLoop yfinance fallback (`tick_loop.py:926-942`) checks FETCH LATENCY (time to download), not DATA AGE (timestamp of last bar). 15-minute-delayed data can pass through if fetched quickly.

Evidence: `tick_loop.py:937` — `elapsed < 3.0` checks fetch time, not `data.index[-1]` age.

**FINDING S-12: Signal queue has no TTL.**
`PrioritySignalQueue(maxsize=50)` at `main.py:1276` has no timestamp-based expiry. Signals can sit in queue indefinitely.

Evidence: `main.py:1276`

### A-6. V2 NEW FINDINGS (Not in V1)

1. **Disconnected pipelines** (C-01) — The #1 critical bug. 30+ gates produce signals never executed.
2. **22 threshold contradictions** (C-02) — V1 found 6, V2 found 22.
3. **InvariantEnforcer is dead code** (C-03) — Never scheduled, never boot-checked.
4. **Overnight kill scope violation** (C-04) — 3x products exempt from mandatory close.
5. **Transition buffer never decremented** (S-06) — Regime flapping unprotected.
6. **ProfitLadder called with atr=0.0** (S-08) — Trailing stops broken.
7. **Correlation families wrong universe** (S-10) — US tickers, not ISA `.L` tickers.
8. **0/8 learning components influence decisions** (C-09) — All theatre or dead.

### A-7. Bottom Line

1. The system has never produced a profitable trade (0/52).
2. **The execution path that matters (TickLoop) is completely disconnected from the sophisticated signal generation (APScheduler paths).**
3. 22 threshold contradictions across 20 categories create nondeterministic behavior.
4. 12-invariant safety net is dead code (never runs).
5. 3x products can be held overnight in violation of plan.
6. 0/8 self-learning components influence decisions.
7. 98 stop-ship items remain unaddressed.

No live capital should be deployed until:
- The disconnected pipeline is unified
- All 22 threshold contradictions resolved to single canonical sources
- InvariantEnforcer actually runs
- The 0/52 track record is explained
- The 100-trade validation gate passes

---

## SECTION B: FULL SYSTEM WIRING CHECK (27 Subsystems)

### B-01. Signal Generation — S15 Daily Target
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — DISCONNECTED FROM EXECUTION** |
| **Evidence** | `main.py:2399` filters `s.strategy == "S15"`, calls `_execute_s15_priority_path()` at `main.py:4221`. S15 scans tickers, applies 30+ gates including data freshness (120s max), multi-confirmation, weighted indicator consensus. ISA SHORT guard at `daily_target.py:1110-1116`. **CRITICAL**: Signals pushed to `_signal_queue.put_nowait()` at `main.py:4234` which is consumed at `main.py:6829-6878` but consumer ONLY logs and pushes to API — it does NOT call `open_position()`. |
| **Fix** | Unify signal generation with TickLoop execution bridge. |

### B-02. Signal Generation — S16 Universal Scanner
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — DISCONNECTED + ISA VIOLATION** |
| **Evidence** | `main.py:1535` loads `UniversalScannerStrategy`. S16 signals routed via `_check_s16_gauntlet()` at `main.py:2424`. Signals pushed to queue at `main.py:2432` but NEVER executed (same dead consumer). SHORT signals emitted by gap scanner (`universal_scanner.py:473`), momentum breakout (line 579), RSI reversal (line 635), sector rotation (line 692) have NO ISA long-only guard. |
| **Fix** | Add ISA long-only guard mirroring S15 pattern. Wire to execution. |

### B-03. Signal Queue Consumer
| Field | Detail |
|-------|--------|
| **Wired?** | **NO — DOES NOT EXECUTE** |
| **Evidence** | Started as asyncio task at `main.py:9245`. Drains queue, logs signals, persists via signal_logger (`main.py:6857-6861`), pushes to API (`main.py:6866-6869`). **Line 6871-6878 are comments/logging only. ZERO calls to `open_position()`**. |
| **Fix** | Either wire consumer to execution OR remove it and use TickLoop-only path. |

### B-04. TickLoop (Command Center)
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — THE ONLY EXECUTION PATH** |
| **Evidence** | `await self._tick_loop.start()` at `main.py:9222`. Position opening confirmed at `tick_loop.py:787` — **THE ONLY LINE THAT CALLS `open_position()` IN PRODUCTION**. TickLoop has its OWN SignalEngine (`tick_loop.py:500-510`) with 6 simpler gates, completely independent of APScheduler S15/S16 pipelines. **CRITICAL**: entire block wrapped in try/except at `main.py:9205-9226` that catches all exceptions and sets `self._tick_loop = None`. Silent failure. |
| **Fix** | TickLoop start failure should be FATAL. Set kill switch on failure. Unify with APScheduler signal generation. |

### B-05. Virtual Trader
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | `open_position()` at `virtual_trader.py:924` with 10+ gates. Chandelier registration at line 1252. Position persistence at line 1265. No ISA direction guard in `open_position()` (relies on upstream filters). Overnight kill at line 1501-1514 only enforces for 5x products. |
| **Fix** | Extend overnight kill to ALL leveraged ETPs. |

### B-06. Chandelier Exit (Profit Ladder)
| Field | Detail |
|-------|--------|
| **Wired?** | **YES — BUT CONFLICTS WITH 2 OTHER LADDERS** |
| **Evidence** | Registered on position open at `virtual_trader.py:1252`. Profit ladder delegates at `virtual_trader.py:1679`. Redis persistence confirmed. Leverage-adjusted ATR multipliers at `chandelier_exit.py:46-50`. **CONFLICT**: `profit_ladder.py` and inline VT logic at line 1666 also exist. |
| **Fix** | Archive `profit_ladder.py` and remove VT inline logic. Chandelier only. |

### B-07. Circuit Breakers
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — THRESHOLD CONTRADICTIONS** |
| **Evidence** | Called at `main.py:2099`. RED triggers emergency flatten at `main.py:2113-2122`. State persisted to Redis (`circuit_breakers.py:393-414`). Hydrated from Redis on startup (`circuit_breakers.py:417-510`). **CONFLICT**: Weekly halt at 8% (`circuit_breakers.py:120`) vs 6% (`risk_sizer.py:52`). Daily halt at 4% (`circuit_breakers.py:84`) vs 3% (`settings.yaml:621`). |
| **Fix** | Resolve to single value: weekly=6%, daily=3%. |

### B-08. ImmutableRiskRules (Constitutional)
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — NOT ACTUALLY IMMUTABLE** |
| **Evidence** | `__setattr__` guard enforces instance immutability (`risk_sizer.py:37`). However, `MAX_WEEKLY_LOSS = 0.06` (line 52) conflicts with CB's 0.08. Class-level mutation is possible (no metaclass guard). `MAX_CONCURRENT_POSITIONS = 3` conflicts with TickLoop's 2 and settings.yaml's 4. |
| **Fix** | Add metaclass `__setattr__` guard. Resolve all contradictions. |

### B-09. Emotional Firewall
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Revenge trading block at `risk_sizer.py:260-268`. Cooldown enforced at line 268. Hard blocks checked at line 380-382. |

### B-10. SessionProtection
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — CONTRADICTS PLAN** |
| **Evidence** | Daily halt at +2.0% (`risk_sizer.py:397`). Weekly halt at -6% (`risk_sizer.py:462`). **Daily halt = daily target = zero headroom**. Plan specifies +2.5%. Weekly halt fires before CB's -8%. |
| **Fix** | Change daily halt to +2.5%. Unify weekly halt to 6%. |

### B-11. Dynamic Sizer (12-Factor Kelly)
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Constitutional cap at `dynamic_sizer.py:64` `_IMMUTABLE_MAX_RISK_PCT = 0.0075` (0.75%). 12 factors documented. Regime multipliers (RISK_OFF=0.0, SHOCK=0.0). Total deployment cap at line 67 = 40%. Bayesian win rate not wired to Kelly (learning output not consumed). |

### B-12. ThresholdRegistry (E-01)
| Field | Detail |
|-------|--------|
| **Wired?** | **NO — COMPLETELY BYPASSED** |
| **Evidence** | `@dataclass(frozen=True)` at `threshold_registry.py:76`. `confidence_floor = 65` at line 99. Documented as "Blood Oath #1" single source of truth. **However, ZERO production modules import from it**. `daily_target.py:75`, `gate_diagnostics.py:62`, `universal_scanner.py:17`, `main.py:2973` all hardcode their own floors. |
| **Fix** | Force all modules to import from ThresholdRegistry. Grep returns zero standalone threshold literals. |

### B-13. Regime Classifier
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — TRANSITION BUFFER BROKEN** |
| **Evidence** | Called at `main.py:2092`. VIX thresholds: HIGH_VOL=25, RISK_OFF=35, SHOCK=45 (`regime_classifier.py:59-61`). 2-session transition buffer (line 123). **CRITICAL**: `decrement_transition_buffer()` defined at line 500 but NEVER CALLED. Regime flapping protection is dead code. |
| **Fix** | Call `decrement_transition_buffer()` at end of each regime eval cycle. |

### B-14. Cross-Asset Macro
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | VIX cache 5-min TTL. CNN Fear & Greed at line 310 (V1 CORRECTION: not crypto). HMM weekly refit. Risk-off detection at line 499-506. |

### B-15. Data Feed Validator
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — CHECKS FETCH TIME, NOT DATA AGE** |
| **Evidence** | Imported at `main.py:81`. S15 has independent data freshness gate at `daily_target.py:625` (120s max). TickLoop checks fetch latency (`tick_loop.py:937` `elapsed < 3.0`) but NOT data timestamp age. 15-minute-delayed data can pass. |
| **Fix** | Check `data.index[-1]` vs `now_utc()`. Reject if age > 120s during active session. |

### B-16. Kill Switch (Dual Implementation)
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — DESYNC ON DEACTIVATE** |
| **Evidence** | File-based at `telegram_bot.py:1839`. Redis at `state_manager.py:311` (no TTL). Both checked at scan entry: `main.py:1720` (file) and `main.py:1731-1736` (Redis). **BUG**: `KillSwitch.deactivate()` only clears file, not Redis. |
| **Fix** | `deactivate()` should also call `state_manager.clear_kill()`. |

### B-17. Portfolio Overseer
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:65`. Can halt bots, force-close positions, create restrictions. |

### B-18. Portfolio Risk Manager
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:78`. Sits between signal qualification and portfolio overseer. |

### B-19. Qualification Pipeline (18-Gate Gauntlet)
| Field | Detail |
|-------|--------|
| **Wired?** | **NO — DEAD CODE (DISCONNECTED)** |
| **Evidence** | S15 signals BYPASS this pipeline entirely via `_execute_s15_priority_path()` (`main.py:4229`). S16 signals also bypass via `_check_s16_gauntlet()`. Only non-S15/non-S16 signals use the full pipeline. **Since S15 and S16 are the only active strategies, the full pipeline is effectively dead code**. Even if signals pass, they go to the dead consumer that doesn't execute. |

### B-20. Confluence Scorer
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:76`. Evaluates agreement across five timeframes (`confluence_scorer.py:62-65`). |

### B-21. Session Boundary Manager
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:82`. Called at `main.py:2129-2138` to check if current phase allows new entries. |

### B-22. Learning Engine
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — OUTPUTS NOT CONSUMED** |
| **Evidence** | Imported at `main.py:66`. Aggregates 4 subsystems (`learning_engine.py:442-448`). With 0/52 win rate, learning has no positive signal. **CRITICAL**: Outputs (win rate, drift signals, ensemble predictions) are NOT consumed by any decision gates. Display only. |

### B-23. ML Meta-Model
| Field | Detail |
|-------|--------|
| **Wired?** | **NO (Intentionally Disabled)** |
| **Evidence** | `_ML_ENABLED = False` at `ml_meta_model.py:56`. AEGIS 0-05 documents broken regime encoding, confidence feature leakage, 43.7% fabricated training data. Returns pass-through when disabled. |

### B-24. Smart Router
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:77`. Scores liquidity, caps position sizes, predicts slippage (`smart_routing.py:118-122`). |

### B-25. Redis State Persistence
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — NOEVICTION GLOBAL** |
| **Evidence** | DB 0 for critical state, DB 1 for telemetry. `maxmemory 400mb --maxmemory-policy noeviction` (`docker-compose.yml:98`). **noeviction is global** — DB 1 telemetry has no separate LRU policy. When 400MB is full, ALL writes fail including position state and CB persistence. |
| **Fix** | Change to `volatile-lru`. Add TTL to DB 1 telemetry keys. |

### B-26. InvariantEnforcer
| Field | Detail |
|-------|--------|
| **Wired?** | **NO — DEAD CODE** |
| **Evidence** | `_run_invariant_runtime_check()` defined at `main.py:1329` but NEVER scheduled. `enforce_boot()` NEVER called. Init wrapped in blanket except at `main.py:1295-1296` labeled "non-critical". All 12 invariants are dead code. |
| **Fix** | Schedule `_run_invariant_runtime_check()` every 60s. Call `enforce_boot()` at startup. Make init failure FATAL. |

### B-27. Incremental Learner
| Field | Detail |
|-------|--------|
| **Wired?** | **NO — THEATRE** |
| **Evidence** | PA classifier trains on every trade (`learning/incremental_learner.py:88`). 52 updates, all label=0 (LOSS). **`predict_proba()` NEVER called by any production module**. Output unused. |

### Section B Summary

| Status | Count | Subsystems |
|--------|-------|------------|
| **YES** | 8 | B-09, B-11, B-14, B-17, B-18, B-20, B-21, B-24 |
| **PARTIAL** | 13 | B-01, B-02, B-04, B-05, B-06, B-07, B-08, B-10, B-13, B-15, B-16, B-22, B-25 |
| **NO (Dead/Disconnected)** | 5 | B-03, B-12, B-19, B-26, B-27 |
| **NO (Intentional)** | 1 | B-23 |
| **CRITICAL** | 5 | B-01 (disconnected), B-02 (disconnected+ISA), B-03 (doesn't execute), B-04 (silent fail), B-26 (12 invariants dead) |

**V2 NEW FINDINGS**: B-01/B-02/B-03 disconnected pipeline, B-12 ThresholdRegistry completely bypassed, B-26 InvariantEnforcer dead code, B-27 IncrementalLearner output unused.

---

## SECTION C: COMMAND TREE AUDIT

### C.1 Who Writes Risk State?

**VERDICT: THREE INDEPENDENT WRITERS WITH NO COORDINATION.**

| Writer | Scope | Persistence | File:Line |
|--------|-------|-------------|-----------|
| StateManager | Global halt, kill switch, session state | Redis `nzt:kill` (no TTL) | `state_manager.py:317-343` |
| VirtualTrader | Position P&L, daily P&L, Phase-9 kill switch | In-memory + SQLite | `virtual_trader.py:460` |
| CircuitBreakerSystem | 8 breakers, weekly/monthly/drawdown halts | Redis DB 0 + in-memory | `circuit_breakers.py:393-414` |

VirtualTrader's Phase-9 P&L kill switch (`virtual_trader.py:460`) is documented as a "legacy duplicate" but still fires BEFORE the canonical circuit breaker check.

### C.2 Who Opens Positions? (FOUR INDEPENDENT PATHS, ONLY ONE EXECUTES)

**V2 CRITICAL FINDING**: Four signal generation paths exist, but only one actually opens positions.

| Path | Trigger | Gates | Destination | Opens Positions? |
|------|---------|-------|-------------|------------------|
| **PATH 1**: S15 Priority | APScheduler 60s → `run_scan()` | 30+ gates (data freshness, multi-confirmation, weighted consensus, ISA guard) | `_signal_queue.put_nowait()` at `main.py:4234` | **NO** |
| **PATH 2**: S16 Medium Gauntlet | APScheduler 60s → S16 pipeline | 18+ gates (no ISA guard) | `_signal_queue.put_nowait()` at `main.py:2432` | **NO** |
| **PATH 3**: General 18-Gate | APScheduler 60s → full pipeline | 18 gates | `_signal_queue.put_nowait()` at `main.py:3341` | **NO** |
| **PATH 4**: TickLoop Brain | TickLoop 30s → SignalEngine | 6 gates (quality, drift, spread) | `virtual_trader.open_position()` at `tick_loop.py:787` | **YES** |

**EVIDENCE FOR DISCONNECTION**:

1. **Signal queue consumer at `main.py:6829-6878`**:
   ```python
   while True:
       signal = await self._signal_queue.get()
       logger.info(f"Signal consumer received: {signal.ticker} {signal.direction}")
       await self._signal_logger.log_signal(signal)  # Line 6857-6861
       await self._push_signal_to_api(signal)        # Line 6866-6869
       # Lines 6871-6878: comments and logging ONLY
       # ZERO calls to open_position()
   ```

2. **TickLoop is THE ONLY execution path**:
   ```python
   # tick_loop.py:787 — THE ONLY LINE THAT OPENS POSITIONS
   await self._virtual_trader.open_position(signal)
   ```

3. **TickLoop has its OWN SignalEngine** (`tick_loop.py:500-510`) that generates PlayScore objects independently of APScheduler.

**IMPACT**: All 30+ sophisticated gauntlet gates in S15/S16/General paths are WASTED. They generate signals that are:
- Logged to SQLite
- Pushed to API dashboard
- Never executed

The TickLoop uses 6 simpler gates before calling `open_position()`. The APScheduler's 60-second scans with 30+ gates are effectively theatre.

**RACE CONDITION (V1 finding still valid)**: Position count check happens OUTSIDE the RLock. Between check and `open_position()` call, the other loop can sneak in a position.

### C.3 Who Owns Final Authority to Block a Trade?

**VERDICT: NO SINGLE AUTHORITY. 14+ INDEPENDENT GATEKEEPERS.**

| Priority | Gatekeeper | File:Line |
|----------|-----------|-----------|
| 1 | KillSwitch (file + process) | `telegram_bot.py:1836-1846` |
| 2 | StateManager kill (Redis) | `state_manager.py:306-315` |
| 3 | Redis health (fail-closed) | `state_manager.py:547-586` |
| 4 | CircuitBreakerSystem | `circuit_breakers.py:549` |
| 5 | DisciplineEngine | `trading_discipline.py:136` |
| 6 | VirtualTrader P&L kill switch | `virtual_trader.py:460` |
| 7 | VirtualTrader CB re-check (T-12) | `virtual_trader.py:950-957` |
| 8 | VirtualTrader portfolio heat | `virtual_trader.py:974-976` |
| 9 | VirtualTrader Stoikov EV gate | `virtual_trader.py:997-1008` |
| 10 | VirtualTrader spread gate | `virtual_trader.py:1111-1114` |
| 11 | VirtualTrader EV gate | `virtual_trader.py:1139-1141` |
| 12-13 | VirtualTrader GPD tail risk (v1+v2) | `virtual_trader.py:1143-1179` |
| 14 | InvariantEnforcer (DEAD CODE) | `invariant_enforcer.py:68` — never runs |

DisciplineEngine is called at THREE separate locations (`main.py:4269`, `main.py:4781`, `main.py:3352`). If a new path is added and forgets the discipline check, it bypasses the "absolute authority."

**V2 NEW FINDING**: InvariantEnforcer is #14 but it's DEAD CODE — never scheduled, never runs.

### C.4 Can Two Modules Disagree and Both Execute?

**VERDICT: NO — but failure mode is OVER-REJECTION, not over-execution.**

The system uses AND-composition: every gatekeeper must pass. However, **the TickLoop execution bridge does NOT check the main.py circuit breaker state directly**. The VirtualTrader's internal CB check at `open_position()` line 950-957 saves this, but ONLY if the CB state has been pushed within 120 seconds.

**V2 ADDITION**: This is now moot because APScheduler paths don't execute anyway — TickLoop is the only path.

### C.5 Can Stale Data Pass Through?

**VERDICT: YES — multiple paths allow stale data to reach execution.**

1. **TickLoop yfinance fallback** (`tick_loop.py:926-942`): Falls back to `yf.download()` which can return 15-minute-delayed data. Staleness check at line 937 only checks FETCH LATENCY (`elapsed < 3.0`), not DATA AGE (`data.index[-1]` timestamp).
2. **OHLCV Cache TTL of 15 seconds** (`tick_loop.py:96`): If yfinance returns stale data, it gets cached for 15s.
3. **Main scan loop** (`main.py:1881-1883`): On weekends or after-hours, last-known price passes through to indicator computation.
4. **Reconciliation loop** (`main.py:6900-6919`): If both feeds return stale values, VirtualTrader updates stops against stale prices.

**V2 NEW FINDING**: Line 937 checks `elapsed < 3.0` (fetch time), not `data.index[-1]` vs `now_utc()` (data age). This is the #1 stale data vector.

### C.6 Can Delayed Signals Still Fire?

**VERDICT: YES — signal queue has no TTL enforcement.**

`PrioritySignalQueue(maxsize=50)` at `main.py:1276` has no timestamp-based expiry. Signals carry `created_at` but it's never checked against a staleness threshold.

TickLoop's drift check at line 770-776 (max 0.5% drift) provides partial protection but doesn't prevent execution on 30-60s old signals.

**V2 ADDITION**: This matters less now that queue consumer doesn't execute, but still affects TickLoop's own signal queue.

### C.7 Can Multiple Exit Conditions Be True Simultaneously?

**VERDICT: YES — LAST-WRITER-WINS in update_prices loop.**

`VirtualTrader._update_prices_locked()` (`virtual_trader.py:1319-1600`) checks exits in priority order with `continue` statements. Items 5-6 (overnight kills) do NOT use `continue` and can accumulate. Chandelier Exit runs SEPARATELY in the reconciliation loop at `main.py:6926-6939`.

**V2 NEW FINDING**: THREE profit ladder implementations can produce conflicting exit signals:
1. Chandelier Exit (Redis-persisted, reconciler loop)
2. `profit_ladder.py` (alternative implementation)
3. VirtualTrader inline logic (`virtual_trader.py:1666-1690`)

### C.8 Can Restarts Clear Kill-Switch State?

**VERDICT: PARTIALLY.**

File-based `_process_killed` is in-memory — CLEARED ON RESTART. File-based kill at `data/KILL_SWITCH` persists if on volume. Redis `nzt:kill` hash (no TTL) SURVIVES RESTART.

**GAP**: TickLoop checks only `self._state.halt_new_signals`, NOT Redis StateManager kill switch directly. Kill switch check in `run_scan()` runs on APScheduler, not at boot.

**V2 NEW FINDING**: `KillSwitch.deactivate()` only clears file, not Redis. Kill switch can persist after deactivation.

### C.9 Can the System Think It Is Safe When a Dependency Is Dead?

**VERDICT: YES — multiple silent failures.**

1. **TickLoop fails silently** (`main.py:9224-9226`): WARNING, not ERROR/CRITICAL. System continues to scan and log signals but opens ZERO positions.
2. **IBKR disconnection degrades silently** (`tick_loop.py:916-924`): Falls back to yfinance with `logger.debug`.
3. **Redis fallback masks failure** (`state_manager.py:128`): In-memory mode reports as "healthy" — restart loses all state.
4. **InvariantEnforcer init failure** (`main.py:1295-1296`): "Non-critical" — 12-invariant safety net silently absent.

**V2 NEW FINDINGS**:
- InvariantEnforcer confirmed DEAD CODE (never scheduled even if init succeeds)
- TickLoop silent failure is THE MOST CRITICAL because it's the only execution path

---

## SECTION D: EXECUTION TIMING AUDIT

### D.1 Current Timing Architecture

Three-layer architecture:

| Layer | Cadence | Purpose | Data Source |
|-------|---------|---------|-------------|
| APScheduler | Fixed times (06:00, 08:00, 12:00, 14:30, 16:00, 19:00, 20:30 UK) + 10s reconciler | Full strategy pipeline (S15/S16) | yfinance (sequential) |
| TickLoop Brain | 30s active / 120s off-hours | Lightweight momentum scan + execution | yfinance/IBKR |
| TickLoop Sniper | 5s | Position monitoring (stops, targets) | IBKR primary, yfinance fallback |

**V2 CRITICAL DIAGNOSIS**: APScheduler is DISCONNECTED from execution. Only TickLoop Brain actually opens positions. The 60-second APScheduler scans with 30+ sophisticated gates are theatre.

### D.2 Exact Timing Bottlenecks (Ranked)

| Rank | Bottleneck | Estimated Latency | File:Line |
|------|-----------|-------------------|-----------|
| 1 | **yfinance Serial Data Fetch** | 24-72s per scan (12 tickers x 2 calls x 1-3s) | `main.py:1878-1899` |
| 2 | **TickLoop SignalEngine.run()** | 6-15s per tick (12 tickers via yfinance) | `tick_loop.py:500-510` |
| 3 | **Sniper Loop Serial Price** | 2-6s for 3 tickers on yfinance fallback | `tick_loop.py:1006-1013` |
| 4 | **S15 Confidence Modifiers** | 2-5s per signal (9+ modifiers) | `main.py:4309-4631` |

**V2 ADDITION**: Rank 4 is now irrelevant because S15 signals don't execute anyway.

### D.3 Is the System Structurally Late?

**VERDICT: YES — 8-20 seconds late on entries, 5-16 seconds late on exits.**

**V2 REVISED ENTRY LATENCY (TickLoop path only — APScheduler doesn't execute)**:

| Component | Latency |
|-----------|---------|
| SignalEngine.run (12 tickers) | 6-15s |
| Quality gates + execution | 1-2s |
| Fresh price + drift check | 1-3s |
| **TOTAL** | **8-20s** |

**APScheduler path (DISCONNECTED — for reference only)**:

| Component | Latency |
|-----------|---------|
| Data ingestion (12 tickers sequential) | 24-72s |
| Strategy evaluation | 2-5s |
| Confidence modifiers | 2-5s |
| Qualification pipeline | 1-2s |
| VirtualTrader.open_position | N/A — never called |
| **TOTAL** | **N/A — signals logged, never executed** |

**Exit Latency (unchanged from V1)**:

| Scenario | Latency |
|----------|---------|
| Best case (IBKR) | 5.1s (sniper interval + IBKR) |
| Worst case (yfinance) | 11-16s (sniper + yfinance) |

**ROOT CAUSE**: System is built on yfinance polling, not event-driven streaming.

### D.4 Recommended Architecture — NOW (Phase B)

1. **Unify Signal Generation with Execution**: Wire APScheduler signal queue to TickLoop execution bridge OR disable APScheduler paths entirely and use TickLoop-only.
2. **Parallelize Data Ingestion** (`main.py:1878-1899`): `ThreadPoolExecutor` for yfinance calls. 12 tickers in ~3-5s instead of 24-72s.
3. **IBKR-Primary Data Path** (`tick_loop.py:903-944`): Auto-reconnect, health monitoring, Telegram alert on disconnection.
4. **Pre-Warm Data Cache at 07:55** (`main.py:5503-5512`): Warm OHLCV cache before 08:00 open.
5. **Data Age Check** (`tick_loop.py:937`): Check `data.index[-1]` vs `now_utc()`, not just fetch latency.

### D.5 Recommended Architecture — LATER (Phase C — Streaming)

1. **Event-Driven IBKR Streaming**: `reqMktData()` push to ring buffer. <100ms latency.
2. **Redis Streams for Signal Pipeline**: Replace in-memory queue with Redis Streams (`XADD`/`XREADGROUP`).
3. **Separate Signal Generator Process**: Decouple data-heavy signal generation from execution.
4. **Rust FFI for Critical Path**: Replace Python `update_prices()` with Rust. <1us per position check.

### D.6 Recommended Cadence Stack

**NOW:**

| Loop | Cadence | Purpose |
|------|---------|---------|
| Signal Generator | 60s (APScheduler) OR disabled | Full strategy pipeline (yfinance parallel) |
| TickLoop Brain | 30s active / 120s off | SOLE execution path |
| Sniper | 5s | Position monitoring (stops, targets) |
| Reconciler | 10s | Position-DB sync + Chandelier Exit |

**LATER:**

| Loop | Cadence | Purpose |
|------|---------|---------|
| Signal Generator | 60s (separate process) | Full strategy pipeline |
| Execution Consumer | Event-driven | Consumes Redis Stream signals |
| Stop Monitor | Event-driven | IBKR price stream → stop check |
| Reconciler | 30s | Redis ↔ SQLite ↔ VirtualTrader sync |

---

## SECTION E: SELF-LEARNING SYSTEM AUDIT

Each component is classified as: **REAL** (functional, influencing decisions with valid data), **PARTIAL** (exists but limited), **THEATRE** (runs but has zero decision influence), or **INVALID** (citation unsupported by code).

**V2 NEW FINDING: 0/8 COMPONENTS INFLUENCE DECISIONS**

### E-1. Incremental Learner (Passive-Aggressive Online Classifier)
- **Classification**: **THEATRE**
- **Evidence**: `learning/incremental_learner.py:25-113`. PA classifier with C=1.0. `partial_fit()` on every trade (line 88). 52 updates, all label=0 (LOSS). Weight vector points entirely toward predicting LOSS.
- **Feature leakage**: `confidence` is an input feature (line 69). If confidence influences the label (which it does — higher confidence trades are taken with larger size), this is circular.
- **V2 WIRING CHECK**: `predict_proba()` NEVER called by any production module. Grep returns 0 calls. **Output unused. ZERO decision influence.**
- **Live Influence**: **ZERO**

### E-2. Drift Detector (Page-Hinkley)
- **Classification**: **PARTIAL (PHANTOM FILE)**
- **Evidence**: `learning/drift_detector.py:27-73`. Lambda=50 (default). At N=52, cumulative sum cannot overcome lambda=50 with uniformly bad outcomes. Threshold too high to trigger.
- **V2 WIRING CHECK**: Reads `drift_report.json` but nothing writes it. The actual drift module is `drift.py` which writes `drift_reports.jsonl` (different file). **Phantom file dependency.**
- **Live Influence**: **ZERO**

### E-3. Bayesian Win Rate Estimator
- **Classification**: **PARTIAL**
- **Evidence**: `learning/bayesian_estimator.py`. Beta(2,2) prior, posterior at N=52 with k=0 wins → Beta(2,54). Posterior mean = 2/56 = 3.6%. MAP = 1/54 = 1.9%. 95% HDI = [0.4%, 11.2%].
- **V2 WIRING CHECK**: Output not wired to Kelly fraction or any gate. Display only. Should feed into `dynamic_sizer.py` Kelly calculation but doesn't.
- **Live Influence**: **ZERO** (but SHOULD be non-zero)

### E-4. Active Learning Weighting
- **Classification**: **THEATRE**
- **Evidence**: `learning/active_weighter.py`. Uses `signal.confidence` (post-modifier) as uncertainty proxy. Should use model prediction uncertainty, not signal confidence.
- **V2 WIRING CHECK**: Feeds weights to IncrementalLearner, which itself has zero influence. Dead weighting for dead model.
- **Live Influence**: **ZERO**

### E-5. Ensemble Diversity
- **Classification**: **THEATRE**
- **Evidence**: `learning/ensemble_diversity.py`. Needs 500+ trades for meaningful diversity measurement. In-sample evaluation only.
- **V2 WIRING CHECK**: `predict_ensemble()` NEVER called by any production module. Output unused.
- **Live Influence**: **ZERO**

### E-6. Reward Shaping
- **Classification**: **INVALID**
- **Evidence**: R-multiples loaded from trade outcomes but never shaped or used for any policy update. Citation to Ng et al. (1999) unsupported by implementation.
- **V2 WIRING CHECK**: No reward-based policy exists. R-multiples computed but never consumed.
- **Live Influence**: **ZERO**

### E-7. Setup Fingerprint Library
- **Classification**: **THEATRE (plan-only)**
- **Evidence**: Referenced in AEGIS plan but no implementation exists. Needs 500+ trades.
- **V2 WIRING CHECK**: No code exists.
- **Live Influence**: **ZERO**

### E-8. LearningEngine (Nightly Activation Cycle)
- **Classification**: **PARTIAL**
- **Evidence**: APScheduler triggers nightly batch at 21:30. Runs tournament, attribution, edge decay. ML-dependent components are inert (disabled).
- **V2 WIRING CHECK**: Only DecayDetector sub-module gates strategies. All other outputs diagnostic only.
- **Live Influence**: **PARTIAL** (DecayDetector can gate strategies, but with 0% WR all strategies decay immediately)

### E-9. Meta-Labelling (De Prado 2018)
- **Classification**: **PARTIAL (correctly disabled)**
- **Evidence**: `ml_meta_model.py:729-794`. `_ML_ENABLED = False` (line 56). Returns pass-through `{veto: False, p_success: 0.5}`. Regime-adaptive thresholds (0.60 trending, 0.70 choppy, 1.0 shock). Feature leakage fixed (J-01). Walk-forward fixed (J-03).
- **Prerequisites**: 200+ genuine trades, regime map fixed, feature leakage removed (done), walk-forward passes.
- **V2 WIRING CHECK**: Correctly disabled. If enabled, would train on 43.7% fabricated data.
- **Live Influence**: **ZERO** (correctly)

### E-10. Walk-Forward Validation
- **Classification**: **PARTIAL**
- **Evidence**: `ml_meta_model.py:491-566`. Expanding-window with 5-day purge/embargo. Correctly implements De Prado CPCV after J-03 fix. With N=52, produces at most 1 fold — insufficient.
- **V2 WIRING CHECK**: Only runs if ML enabled (currently disabled). Output unused.
- **Live Influence**: **ZERO**

### E-11. SHAP / Feature Stability
- **Classification**: **THEATRE (correctly gated)**
- **Evidence**: `ml_meta_model.py:872-1072`. TreeExplainer, rank drift threshold=5, minimum 4 features kept. Never executed because ML is disabled.
- **V2 WIRING CHECK**: Only runs if ML enabled. Output diagnostic only.
- **Live Influence**: **ZERO**

### E-12. HMMRegimeOverlay (V2 NEW — ORPHANED)
- **Classification**: **INVALID (ORPHANED)**
- **Evidence**: `learning/hmm_regime_overlay.py` exists but NEVER IMPORTED by any module. The real HMM is `core/regime_hmm.py`. This is an abandoned duplicate.
- **V2 WIRING CHECK**: `grep -rn "hmm_regime_overlay"` returns only the file itself, no imports.
- **Live Influence**: **ZERO**

### Summary Matrix (V2 Extended)

| # | Component | Classification | Live Influence | Wiring | Min Data | Current N |
|---|-----------|---------------|---------------|--------|----------|-----------|
| 1 | Incremental Learner | THEATRE | **ZERO** | `predict_proba()` never called | 200+ trades, >30% WR | 52 @ 0% WR |
| 2 | Drift Detector | PARTIAL | **ZERO** | Reads phantom file `drift_report.json` | ~100+ trades | 52 |
| 3 | Bayesian Win Rate | PARTIAL | **ZERO** (should be non-zero) | Not wired to Kelly | 1+ | 52 |
| 4 | Active Learning | THEATRE | **ZERO** | Feeds dead model | N/A (wrong input) | 52 |
| 5 | Ensemble Diversity | THEATRE | **ZERO** | `predict_ensemble()` never called | 500+ trades | 52 |
| 6 | Reward Shaping | INVALID | **ZERO** | No policy exists | N/A | N/A |
| 7 | Setup Fingerprint | THEATRE | **ZERO** | No implementation | 500+ trades | 0 |
| 8 | LearningEngine | PARTIAL | **PARTIAL** | DecayDetector only | N/A | N/A |
| 9 | Meta-Labelling | PARTIAL (disabled) | **ZERO** (correctly) | Correctly disabled | 200+ trades | 52 |
| 10 | Walk-Forward | PARTIAL | **ZERO** | ML disabled | 200+ trades | 52 |
| 11 | SHAP Stability | THEATRE (gated) | **ZERO** | ML disabled | 4 windows | 0 |
| 12 | HMMRegimeOverlay | INVALID (orphaned) | **ZERO** | Never imported | N/A | N/A |

**V2 VERDICT: 0/12 components are REAL. 5 THEATRE, 4 PARTIAL, 2 INVALID, 1 correctly disabled.**

**V2 NEW FINDING**: The "self-learning system" is a façade. Zero components influence trading decisions. All are either:
1. Running but outputs unused (THEATRE)
2. Partially functional but not wired to gates (PARTIAL)
3. Orphaned/phantom dependencies (INVALID)
4. Correctly disabled due to broken implementation

---

## SECTION F: ACADEMIC + INSTITUTIONAL RESEARCH CHECK

*(Unchanged from V1 — V1 analysis was thorough on this section)*

### F-1. Leveraged ETP Decay (Ben-David, Franzoni & Moussawi 2018)
- **Assessment**: **Requires stronger caveat**
- **Issue**: `dynamic_sizer.py:1777-1799` uses fixed 2bps/day drag. Actual decay = leverage * (leverage-1) * sigma^2 / 2. On a 4% vol day, a 3x ETP loses ~24bps, not 2bps.
- **Chandelier leverage adjustment** (`chandelier_exit.py:46-60`) is correctly calibrated.

### F-2. Momentum Evidence (Jegadeesh & Titman 1993)
- **Assessment**: **Overstated**
- **Issue**: JT93 is 3-12 month cross-sectional on individual stocks. NZT-48 is intraday, single-asset directional, on leveraged ETPs. Different phenomenon entirely. The Barroso & Santa-Clara (2015) crash guard is designed for monthly portfolios, not intraday.

### F-3. Volume/Liquidity (Chordia, Roll & Subrahmanyam 2001)
- **Assessment**: **Correctly applied** with caveat
- **Issue**: RVOL gate at 0.60-0.80 is reasonable. However, for LSE leveraged ETPs, volume is partially synthetic (market-maker provided). ADV may not reflect actual depth.

### F-4. ADX / Technical Rules (Brock, Lakonishok & LeBaron 1992)
- **Assessment**: **Partially misapplied**
- **Issue**: Brock et al. does not use ADX. ADX proxy in walkforward (`range/ATR * 25`) is not actual ADX (Wilder 1978). Park & Irwin (2007) shows declining profitability after 1990.

### F-5. Kelly Criterion (Kelly 1956, Thorp 2006)
- **Assessment**: **Correctly applied**
- **Evidence**: Adaptive Kelly fraction (`dynamic_sizer.py:934-972`): quarter-Kelly (<50 trades), ramp to half-Kelly (50-200). Beta(2,2) stranger penalty. At 0% WR, Kelly correctly sizes to zero.

### F-6. Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)
- **Assessment**: **Correctly applied**
- **Evidence**: `core/quant_math/dsr.py:11-38`. Formula matches paper. DSR threshold p >= 0.95 in Go/No-Go gate (`sprint6_live_gate.py:203`). `num_trials=20` assumption reasonable.

### F-7. Meta-Labelling Walk-Forward (De Prado 2018)
- **Assessment**: **Correctly applied** (after J-03 fix)
- **Evidence**: Expanding-window with 5-day purge/embargo at `ml_meta_model.py:491-566`. Replaced shuffled StratifiedKFold.

### F-8. HMM / Regime Switching (Ang & Bekaert 2002)
- **Assessment**: **Partially misapplied**
- **Issue**: HMM trained on ETP's own returns, not underlying index. Leveraged ETP returns are non-Gaussian. Confidence adjustment [-0.2, +0.2] is negligible.

### F-9. Online Learning (Crammer et al. 2006)
- **Assessment**: **Partially misapplied**
- **Issue**: PA algorithm is correct. Application context wrong: noisy binary labels, confidence leakage, all-loss training data.

### F-10. Stop / Ladder Design (Le Beau 1999)
- **Assessment**: **Correctly applied**
- **Evidence**: `chandelier_exit.py:1-80`. 5-rung profit ladder. Le Beau attribution accurate. Partial banking percentages are practitioner innovation, not academic.

### F-11. Market-Maker Adaptation (Almgren & Chriss 2001)
- **Assessment**: **Unsupported**
- **Issue**: No implementation shortfall model. No bid-ask spread monitoring at order time. No market-impact estimation. 10% participation rate is for US large-caps, not LSE ETPs.

### F-12. Stale-Data Hazard
- **Assessment**: **Requires stronger caveat**
- **Issue**: T-10 lunch block is a narrow fix. No systematic stale-data framework. **V2 ADDITION**: Data age not checked, only fetch latency.

### F-13. Paper Alpha Collapse
- **Assessment**: **Unsupported**
- **Issue**: No slippage model, no fill-rate modeling, no adverse selection. LSE ETPs have 10-50bps spreads. System runs on EC2 US-East with 80-100ms transcontinental latency.

### Summary Table

| # | Mechanism | Assessment |
|---|-----------|-----------|
| 1 | Leveraged ETP decay | Requires stronger caveat |
| 2 | Momentum (JT93) | Overstated |
| 3 | Volume/liquidity | Correctly applied + caveat |
| 4 | ADX / technical rules | Partially misapplied |
| 5 | Kelly criterion | Correctly applied |
| 6 | DSR / multiple testing | Correctly applied |
| 7 | Meta-labelling walk-forward | Correctly applied |
| 8 | HMM / regime switching | Partially misapplied |
| 9 | Online learning (PA) | Partially misapplied |
| 10 | Stop / ladder design | Correctly applied |
| 11 | Market-maker adaptation | Unsupported |
| 12 | Stale-data hazard | Requires stronger caveat |
| 13 | Paper alpha collapse | Unsupported |

**4 correctly applied, 3 partially misapplied, 2 overstated/unsupported, 2 require caveat, 2 unsupported.**

---

## SECTION G: PLAN vs CODE TRUTH TABLE (V2 EXTENDED)

**V2 adds 8 new rows for newly discovered contradictions.**

| # | Plan Claim | Code Reality | Status | Evidence |
|---|-----------|-------------|--------|----------|
| 1 | Confidence floor = 65 (ThresholdRegistry) | `threshold_registry.py:99` = 65, BUT `gate_diagnostics.py:62` = 55, `main.py:2973` = 60, `universal_scanner.py:17` = 58 | **CONTRADICTION (4 values)** | V2: 4 values, not 3 |
| 2 | Weekly halt = -8% (circuit breakers) | `circuit_breakers.py:120` = 8%, `risk_sizer.py:52` = 6% | **CONTRADICTION** | Both exist |
| 3 | Max concurrent positions = 4 | `settings.yaml:623` = 4, `risk_sizer.py:53` = 3, `tick_loop.py:286` = 2, `threshold_registry.py:97` = 3, `universal_scanner.py` = 5 | **CONTRADICTION (5 values)** | V2: 5 values, not 3 |
| 4 | RVOL FAST = 0.30 | `daily_target.py:67` = 0.60, comment: "0.30 was suicidal" | **PARTIAL** | Code deviated with justification |
| 5 | Max signals/day = 4 | `daily_target.py:74` = 3, `gate_diagnostics.py:61` = 1 | **CONTRADICTION** | 2 different values |
| 6 | Lunch penalty = -10 flat | `daily_target.py:530-534` = 0.85x multiplier | **PARTIAL** | Different mechanism |
| 7 | SessionProtection halt = +2.5% | `risk_sizer.py:397` = +2.0% | **PARTIAL** | 50bps below plan |
| 8 | Redis = allkeys-lru | `docker-compose.yml:98` = noeviction | **CONTRADICTION** | Opposite policies |
| 9 | ADX FAST = 15, SLOW = 20 | `daily_target.py:81-83` = 15/20 | **VERIFIED** | Matches plan |
| 10 | ISA SHORT guard | S15: `daily_target.py:1110-1116` ✓, S16: **MISSING** | **PARTIAL** | S16 has no guard |
| 11 | ML disabled (AEGIS 0-05) | `ml_meta_model.py:56` `_ML_ENABLED = False` | **VERIFIED** | Matches plan |
| 12 | Timezone = Europe/London | `settings.yaml:9` = "Europe/London" | **VERIFIED** | Fixed (was US/Eastern) |
| 13 | VIX hysteresis = 5% deadband | `circuit_breakers.py:96` = 15% | **PARTIAL** | More conservative than plan |
| 14 | Kill switch dual implementation | File-based + Redis, both checked | **PARTIAL (DESYNC)** | V2: deactivate() only clears file |
| 15 | Equity denominator updates daily | `circuit_breakers.py:755-779` fixed. `sheets_logger.py:57` and `dynamic_sizer.py:214` still default 10000 | **PARTIAL** | CB fixed, others not |
| 16 | Consecutive loss query scoped | `main.py:1377-1404` uses ScopedQuery | **VERIFIED** | Fixed |
| 17 | VIX default = 20 (not 0) | Fail-closed at 20 when fetch fails | **VERIFIED** | Fixed |
| 18 | Signal queue = async | `main.py:1276` PrioritySignalQueue(maxsize=50) | **VERIFIED** | Fixed |
| 19 | ISA eligibility enforced | `main.py:1851` filters ISA tickers | **VERIFIED** | Fixed |
| 20 | Regime map = 8 states | `regime_classifier.py:59-61` = 8 states | **VERIFIED** | Matches |
| 21 | Correlation families defined | `dynamic_sizer.py:1302-1313` — US tickers only, ISA `.L` tickers never match | **PARTIAL** | V2: Families exist but wrong universe |
| 22 | CB persistence = Redis | `circuit_breakers.py:393-414` persists to Redis | **VERIFIED** | Fixed |
| 23 | ML feature leakage fixed (J-01) | `ml_meta_model.py:101-103` confidence removed | **VERIFIED** | Fixed |
| 24 | Walk-forward fixed (J-03) | `ml_meta_model.py:491-566` expanding-window | **VERIFIED** | Fixed |
| 25 | Fear & Greed = CNN equities | `cross_asset_macro.py:310` fetches CNN | **VERIFIED** | V1 correction: not crypto |
| 26 | Transition buffer exists | `regime_classifier.py:500-505` defined | **PARTIAL** | V2: Defined but `decrement_transition_buffer()` NEVER CALLED |
| 27 | ImmutableRiskRules immutable | Instance `__setattr__` guard exists. Class-level mutation possible. | **PARTIAL** | V2: No metaclass guard |
| 28 | Overnight kill for ALL ETPs | Only 5x enforced; 3x can hold overnight | **PARTIAL** | V2: Code at `virtual_trader.py:1501-1514` |
| 29 | 100-trade validation gate | No executable code exists | **NOT IMPLEMENTED** | Plan-only |
| 30 | Profit ladder = Chandelier only | 3 implementations exist | **CONTRADICTION** | V2: `chandelier_exit.py`, `profit_ladder.py`, VT inline |
| **31** | **Daily loss limit = 3%** | **`settings.yaml:621` = 3%, `circuit_breakers.py:84` L3 = 4%** | **CONTRADICTION** | **V2 NEW** |
| **32** | **Spread veto multiplier = 2.5x** | **`threshold_registry.py:105` = 2.5x, `daily_target.py:1088` = 1.8x** | **CONTRADICTION** | **V2 NEW** |
| **33** | **Consecutive loss tier 2 = 5** | **`threshold_registry.py:102` = 5, `circuit_breakers.py:89` = 4** | **CONTRADICTION** | **V2 NEW** |
| **34** | **Staleness limit = 120s** | **`threshold_registry.py:112` = 120s, `invariant_enforcer.py:115` = 300s** | **CONTRADICTION** | **V2 NEW** |
| **35** | **InvariantEnforcer runs at boot + 60s** | **Never scheduled, never boot-checked** | **NOT IMPLEMENTED** | **V2 NEW** |
| **36** | **Signal queue consumer executes signals** | **Only logs and pushes to API** | **DISCONNECTED** | **V2 NEW** |
| **37** | **APScheduler paths execute trades** | **All push to dead consumer** | **DISCONNECTED** | **V2 NEW** |
| **38** | **Profit ladder bank_pct = 0.33 all rungs** | **Code: 0.0/0.15/0.33/0.50/0.0** | **CONTRADICTION** | **V2 NEW** |

**Summary: 14 VERIFIED, 13 PARTIAL, 9 CONTRADICTION, 2 NOT IMPLEMENTED, 2 DISCONNECTED (V2)**

**V2 adds 8 new rows (31-38) documenting newly discovered contradictions and disconnections.**

---

## SECTION H: STOP-SHIP ITEMS (Top 30 — V2 Extended)

### P0 — Critical (15 items — V2 adds 3 new)

| ID | Title | Blast Radius | Evidence | Fix |
|----|-------|-------------|----------|-----|
| **H-01** | **DISCONNECTED SIGNAL PIPELINES** | **ALL APScheduler signals wasted** | **`main.py:6829-6878` consumer doesn't execute** | **Wire consumer to execution OR use TickLoop-only** |
| **H-02** | **InvariantEnforcer DEAD CODE** | **12-invariant safety net absent** | **Never scheduled, never boot-checked** | **Schedule runtime check every 60s, call enforce_boot() at startup** |
| **H-03** | **TickLoop silent failure** | **System opens ZERO trades** | **`main.py:9224-9226` WARNING only** | **Make failure FATAL, set kill switch** |
| H-04 | Weekly halt contradiction (6% vs 8%) | Risk management integrity | `risk_sizer.py:52` vs `circuit_breakers.py:120` | Decide 6%. Update CB to 0.06. |
| H-05 | Max positions contradiction (2 vs 3 vs 4 vs 5) | Concentration risk | 5 different values | Align to 3 everywhere. |
| H-06 | ImmutableRiskRules class-level mutation | Constitutional controls bypassable | `risk_sizer.py:71-94` no metaclass | Add metaclass `__setattr__`. |
| H-07 | Redis noeviction global | All state persistence fails at 400MB | `docker-compose.yml:98` | Change to volatile-lru + TTL on telemetry. |
| H-08 | Three profit ladder systems | Conflicting exit signals | `chandelier_exit.py`, `profit_ladder.py`, `virtual_trader.py:1666` | Confirm Chandelier as sole. Archive others. |
| **H-09** | **Transition buffer never decremented** | **Phantom regime states** | **`regime_classifier.py:500-505` — 0 callers** | **Call at end of each regime eval cycle.** |
| **H-10** | **Data age not checked** | **Entries on 15-min-old data** | **`tick_loop.py:937` checks fetch time, not data age** | **Check `data.index[-1]` vs `now_utc()`, reject if > 120s** |
| H-11 | S16 ISA SHORT guard missing | Illegal short-selling | `universal_scanner.py:473,527,579,635,692` | Add ISA long-only guard. |
| H-12 | Kill switch desync on deactivate | Redis kill persists | `telegram_bot.py:1857-1863` only clears file | Call `state_manager.clear_kill()`. |
| H-13 | Confidence floor contradiction (4+ values) | Nondeterministic filtering | 55/58/60/65/75 across 5 locations | Force ThresholdRegistry import everywhere. |
| H-14 | Overnight kill scope violation | 3x products held overnight | `virtual_trader.py:1501-1514` only checks 5x | Extend to ALL leveraged ETPs. |
| H-15 | 100-trade validation gate not executed | All subsequent work gated | Plan RK-01 | Execute 100 trades post timing fixes. |

### P1 — High (15 items — V2 adds 7 new)

| ID | Title | Evidence |
|----|-------|----------|
| H-16 | Equity denominator partial fix | `sheets_logger.py:57`, `dynamic_sizer.py:214` default 10000 |
| H-17 | RVOL FAST deviated from plan | `daily_target.py:67` = 0.60 vs plan 0.30 |
| H-18 | Max signals/day = 3 vs plan's 4 | `daily_target.py:74` |
| H-19 | Lunch multiplier differs from plan | `daily_target.py:530-534` = 0.85x vs -10 |
| H-20 | Stale comment in cross_asset_macro | Line 298 says "Alternative.me" but uses CNN |
| H-21 | VIX hysteresis 15% vs plan's 5% | `circuit_breakers.py:96` |
| H-22 | Monthly halt implemented but not reconciled | `circuit_breakers.py:121` = -15% |
| **H-23** | **ThresholdRegistry completely bypassed** | **ZERO production imports** |
| **H-24** | **Correlation families wrong universe** | **US tickers, ISA `.L` never match** |
| **H-25** | **ProfitLadder.evaluate() called with atr=0.0** | **`main.py:7118` — trailing stops broken** |
| **H-26** | **Signal queue no TTL** | **Signals can sit indefinitely** |
| **H-27** | **0/8 learning components influence decisions** | **All THEATRE or PARTIAL** |
| **H-28** | **Daily loss limit contradiction (3% vs 4%)** | **`settings.yaml:621` vs `circuit_breakers.py:84`** |
| **H-29** | **Spread veto multiplier contradiction (1.8x vs 2.5x)** | **`daily_target.py:1088` vs `threshold_registry.py:105`** |
| **H-30** | **Staleness limit contradiction (120s vs 300s)** | **`threshold_registry.py:112` vs `invariant_enforcer.py:115`** |

---

## SECTION I: TIMING/GATING TRIAGE (Sprint-Ready)

**V2 adds 4 new cards (TG-13 through TG-16).**

### TG-01: DISCONNECTED PIPELINES → UNIFIED EXECUTION
- **Priority**: P0 | **Effort**: 8h | **Dependencies**: None
- **Files**: `main.py:6829-6878`, `tick_loop.py:500-787`
- **Win-Rate Impact**: CRITICAL — 30+ sophisticated gates currently wasted
- **Accept**: Wire signal queue consumer to `virtual_trader.open_position()` OR disable APScheduler paths and use TickLoop-only. Deduplication by (ticker, direction, strategy, 60s window).

### TG-02: InvariantEnforcer → ACTUAL ENFORCEMENT
- **Priority**: P0 | **Effort**: 3h | **Dependencies**: None
- **Files**: `main.py:1295-1296,1329`, `invariant_enforcer.py:68`
- **Win-Rate Impact**: High — 12-invariant safety net currently absent
- **Accept**: Schedule `_run_invariant_runtime_check()` every 60s. Call `enforce_boot()` at startup. Make init failure FATAL (not "non-critical").

### TG-03: TickLoop Silent Failure → Fatal Error
- **Priority**: P0 | **Effort**: 1h | **Dependencies**: None
- **Files**: `main.py:9222-9225`
- **Win-Rate Impact**: Liveness — system opens ZERO trades if TickLoop dies
- **Accept**: TickLoop failure activates kill switch + Telegram alert. `logger.critical`, not `warning`.

### TG-04: Stale Data Age Check
- **Priority**: P0 | **Effort**: 2h | **Dependencies**: None
- **Files**: `tick_loop.py:930-940`
- **Win-Rate Impact**: High — entries on 15-min-old yfinance data are fiction
- **Accept**: After fetch, check `data.index[-1]` vs `now_utc()`. Reject if age > 120s during active session.

### TG-05: Sequential → Parallel yfinance Fetch
- **Priority**: P0 | **Effort**: 4h | **Dependencies**: None
- **Files**: `data_hub/sources/yfinance_source.py`, `data_hub/hub.py`, `signal_engine/engine.py`
- **Win-Rate Impact**: Critical — 12 tickers in ~3-5s instead of 24-72s
- **Accept**: `ThreadPoolExecutor(max_workers=4)`. Total fetch < 4 seconds.

### TG-06: Confidence Floor Unification
- **Priority**: P0 | **Effort**: 3h | **Dependencies**: TG-14 (ThresholdRegistry enforcement)
- **Files**: `daily_target.py:75`, `risk_sizer.py:57`, `gate_diagnostics.py:62`, `main.py:2973,4842`, `universal_scanner.py:17`
- **Win-Rate Impact**: Medium-High — eliminates nondeterministic filtering
- **Accept**: All modules import from `ThresholdRegistry`. Grep returns zero standalone `_MIN_CONFIDENCE` literals.

### TG-07: Weekly Halt + Max Positions + SessionProtection Unification
- **Priority**: P0 | **Effort**: 2h | **Dependencies**: None
- **Files**: `circuit_breakers.py:120`, `risk_sizer.py:52,397,462`, `settings.yaml:623`, `tick_loop.py:286`
- **Win-Rate Impact**: Low (risk management, not alpha)
- **Accept**: Weekly = 6%. Max positions = 3. SessionProtection = +2.5%. Boot with zero invariant failures.

### TG-08: S16 ISA SHORT Guard
- **Priority**: P0 | **Effort**: 1h | **Dependencies**: None
- **Files**: `universal_scanner.py:473,527,579,635,692`, `main.py:4841-4845`
- **Win-Rate Impact**: None (legality issue — UK ISA prohibits short selling)
- **Accept**: SHORT signal for ISA ticker → rejected. Inverse ETPs exempt.

### TG-09: Redis volatile-lru Migration
- **Priority**: P0 | **Effort**: 2h | **Dependencies**: None
- **Files**: `docker-compose.yml:98`, `core/redis_config.py`
- **Win-Rate Impact**: None directly — prevents state persistence failure
- **Accept**: `noeviction` → `volatile-lru`. DB 1 telemetry keys get 1h TTL. DB 0 keys persist.

### TG-10: Signal Queue TTL
- **Priority**: P1 | **Effort**: 2h | **Dependencies**: TG-01
- **Files**: `tick_loop.py:276`, `main.py:1276`
- **Win-Rate Impact**: Medium — prevents execution on stale signals
- **Accept**: Signals carry `created_at` timestamp. Reject if > 30s old.

### TG-11: IBKR Reconnection Loop
- **Priority**: P1 | **Effort**: 3h | **Dependencies**: None
- **Files**: `tick_loop.py:310-320`, `ibkr_source.py`
- **Win-Rate Impact**: Medium — prevents permanent yfinance fallback
- **Accept**: TickLoop checks `ibkr_client.connected` every 60s. Auto-reconnect on disconnect.

### TG-12: Dual Signal Generator Consolidation
- **Priority**: P1 | **Effort**: 8h | **Dependencies**: TG-01, TG-05, TG-10
- **Files**: `main.py` (scheduler), `tick_loop.py` (Brain)
- **Win-Rate Impact**: Medium — eliminates duplicate/contradictory signals
- **Accept**: Single signal bus. Deduplication by (ticker, direction, strategy, 60s window).

### TG-13: Transition Buffer Decrement (V2 NEW)
- **Priority**: P0 | **Effort**: 1h | **Dependencies**: None
- **Files**: `regime_classifier.py:500`
- **Win-Rate Impact**: Medium — prevents phantom regime states
- **Accept**: Call `decrement_transition_buffer()` at end of each regime eval cycle. Regime flapping properly damped.

### TG-14: ThresholdRegistry Enforcement (V2 NEW)
- **Priority**: P0 | **Effort**: 4h | **Dependencies**: None
- **Files**: All modules with hardcoded thresholds
- **Win-Rate Impact**: High — establishes single source of truth
- **Accept**: Force all modules to import from ThresholdRegistry. Add import guard at boot. Grep returns zero threshold literals.

### TG-15: Overnight Kill ALL ETPs (V2 NEW)
- **Priority**: P0 | **Effort**: 1h | **Dependencies**: None
- **Files**: `virtual_trader.py:1501-1514`
- **Win-Rate Impact**: Low (plan compliance)
- **Accept**: Extend overnight kill to ALL leveraged ETPs, not just 5x. 3x products close before 16:00.

### TG-16: Kill Switch Deactivate Unification (V2 NEW)
- **Priority**: P1 | **Effort**: 0.5h | **Dependencies**: None
- **Files**: `telegram_bot.py:1857-1863`
- **Win-Rate Impact**: Low (operational)
- **Accept**: `KillSwitch.deactivate()` also calls `state_manager.clear_kill()`. Both file and Redis cleared.

---

## SECTION J: PLAN PATCHES

**V2 adds 3 new patches (J-09 through J-11).**

### J-01: Weekly Halt
- **Document**: `aegis/07_RISK.md:16`
- **Current**: `-8.0%` | **Proposed**: `-6.0%`
- **Justification**: Code reality in `risk_sizer.py:52` = 6%. Constitution wins.

### J-02: Max Concurrent Positions
- **Document**: `aegis/01_STOP_SHIP.md:151`
- **Current**: `4` | **Proposed**: `3`
- **Justification**: `ImmutableRiskRules.MAX_CONCURRENT_POSITIONS = 3` is constitutional.

### J-03: RVOL FAST Threshold
- **Document**: `aegis/03_EXECUTION_TIMING.md:199`
- **Current**: `0.30` | **Proposed**: `0.60`
- **Justification**: Code at `daily_target.py:67` already corrected. "0.30 was suicidal on LSE ETPs."

### J-04: Max Signals Per Day
- **Document**: `aegis/01_STOP_SHIP.md` (SK-04)
- **Current**: `1` | **Proposed**: `3`
- **Justification**: Code at `daily_target.py:74` = 3. Single-fire killed recovery trades.

### J-05: Lunch Penalty
- **Document**: `aegis/01_STOP_SHIP.md:155`
- **Current**: `-10 flat` | **Proposed**: `0.85x multiplier`
- **Justification**: Code at `daily_target.py:530-535`. Proportional scaling is more principled.

### J-06: Redis Policy
- **Document**: `aegis/04_HARDENING.md:274`
- **Current**: `allkeys-lru` | **Proposed**: `volatile-lru` with TTL on telemetry, P0 priority
- **Justification**: Neither allkeys-lru (evicts position state) nor noeviction (bricks all writes) is correct.

### J-07: SessionProtection Halt
- **Document**: `aegis/01_STOP_SHIP.md:141`
- **Current**: `+2.0%` | **Proposed**: `+2.5%`
- **Justification**: Halt at target = zero headroom for costs.

### J-08: VIX Hysteresis
- **Document**: `aegis/07_RISK.md`
- **Current**: `5% deadband` | **Proposed**: `15% deadband`
- **Justification**: Code at `circuit_breakers.py:96` = 15%. More conservative. Document the deviation.

### J-09: Overnight Kill Scope (V2 NEW)
- **Document**: `aegis/03_EXECUTION_TIMING.md:44`
- **Current**: `ALL ETPs` | **Actual**: Only 5x enforced
- **Justification**: Code at `virtual_trader.py:1501-1514` only checks 5x. 3x products can hold overnight. Either extend code to ALL or update plan to "5x only."

### J-10: Daily Loss Limit (V2 NEW)
- **Document**: `aegis/07_RISK.md`
- **Current**: `3%` (settings.yaml) and `4%` (circuit breakers L3)
- **Proposed**: Unify to `3%`
- **Justification**: Settings.yaml is configuration layer. Circuit breakers should respect it.

### J-11: Signal Queue Consumer (V2 NEW)
- **Document**: `aegis/02_ARCHITECTURE.md` (signal flow diagram)
- **Current**: Implies consumer executes trades
- **Actual**: Consumer only logs and pushes to API
- **Justification**: Document that TickLoop is SOLE execution path. APScheduler paths generate candidates only.

---

## SECTION K: MINIMUM VIABLE SYSTEM

**V2 adds 5 new checklist items (K-14 through K-18).**

What must be TRUE before the 100-trade validation gate can begin:

- [ ] **K-01**: TickLoop is alive and crash-loud on failure (`main.py:9224`)
- [ ] **K-02**: ONE confidence floor everywhere (65 from ThresholdRegistry)
- [ ] **K-03**: ONE weekly halt threshold (6% everywhere)
- [ ] **K-04**: ONE max concurrent positions value (3 everywhere)
- [ ] **K-05**: Data age checked, not just fetch latency (`tick_loop.py:936-938`)
- [ ] **K-06**: No SHORT signals in ISA context (`universal_scanner.py` — S16 guard)
- [ ] **K-07**: Redis cannot brick itself (volatile-lru + TTL on telemetry)
- [ ] **K-08**: ImmutableRiskRules actually immutable (metaclass guard)
- [ ] **K-09**: ONE profit ladder system (Chandelier only)
- [ ] **K-10**: Signal list not mutated during iteration (`main.py:1929`)
- [ ] **K-11**: Transition buffer decrement called somewhere (`regime_classifier.py:500`)
- [ ] **K-12**: InvariantEnforcer runs at boot and every 60s
- [ ] **K-13**: 100-trade validation gate exists as executable code
- [ ] **K-14**: Signal queue consumer EITHER executes OR is removed (V2 NEW)
- [ ] **K-15**: APScheduler paths EITHER wire to execution OR are disabled (V2 NEW)
- [ ] **K-16**: ThresholdRegistry enforced as single source of truth (V2 NEW)
- [ ] **K-17**: Overnight kill extends to ALL ETPs, not just 5x (V2 NEW)
- [ ] **K-18**: Kill switch deactivate() clears both file AND Redis (V2 NEW)

---

## ADDENDUM 1: CRITICAL QUESTIONS

**V2 adds 10 new questions (14-23).**

**The questions this system cannot yet answer:**

1. **What is the actual signal-to-fill latency?** No metric tracks elapsed time from signal creation to `open_position()`. The 8-20s estimate is theoretical.

2. **Which profit ladder actually closes trades?** Three exist. Which one fired on each of the 52 losses?

3. **What is the actual yfinance data age at execution?** Fetch latency is checked, data timestamp is not. Were entries based on 15-min-old prices?

4. **Does InvariantEnforcer actually run?** Initialized with blanket except (`main.py:1296`). Any log evidence of 60s execution? **V2 ANSWER: NO — never scheduled, confirmed dead code.**

5. **What regime was active during each of the 52 trades?** Transition buffer never decrements — were trades entered during phantom states?

6. **What is ISA ETP correlation?** `dynamic_sizer.py:1302-1313` has US tickers only. ISA `.L` tickers never match. Correlation penalty has NEVER fired.

7. **What spread did each trade pay?** VirtualTrader uses proxy spread model. Real LSE ETP spreads vary 10-50bps intraday.

8. **Can +2.0% daily net actually be reached after costs?** SessionProtection halts at +2.0% gross. With 3 trades at 15-25bps round-trip, max achievable net is ~1.25-1.55%.

9. **What happens when Redis hits 400MB?** Does Chandelier exit handle write failures? Does circuit breaker state survive?

10. **Is 0.75% risk-per-trade appropriate for 3x ETPs?** 0.75% on 3x means underlying moves 0.25% to stop. Entry slippage alone can be 10-25bps (40-100% of risk budget).

11. **What was the confidence score distribution of the 52 losers?** If median confidence was 70+, the scoring system is fundamentally miscalibrated.

12. **Can APScheduler and TickLoop produce contradictory signals?** Both run independently. No deduplication exists. **V2 ANSWER: YES, but APScheduler signals don't execute anyway.**

13. **What is max drawdown from 3 concurrent 3x ETPs reversing?** Nominal max = 2.25%. With gap risk on 3x, actual could be 5-7%.

14. **V2: How many APScheduler signals were generated vs TickLoop signals?** Which path produced the 52 losing trades? **CRITICAL: Were any APScheduler signals ever executed?**

15. **V2: What percentage of TickLoop ticks use IBKR vs yfinance?** If yfinance fallback is primary data source, latency is structurally >10s.

16. **V2: What is the actual age distribution of data at execution?** Percentiles: p50, p90, p99. How often is data >60s old? >120s? >300s?

17. **V2: What happens to position state when Redis noeviction triggers?** Does VirtualTrader fail silently? Do positions become zombie trades?

18. **V2: Why are 0/8 learning components wired to decision gates?** Is this intentional (waiting for data) or architectural oversight?

19. **V2: What is the confidence score of signals that pass TickLoop gates vs APScheduler gates?** Are TickLoop's 6 gates more or less selective than APScheduler's 30+?

20. **V2: What is the correlation between 3x ISA ETPs during the 52 trades?** If all moved together, diversification is zero.

21. **V2: How many times did Chandelier Exit vs profit_ladder.py vs VT inline logic trigger?** Which ladder system actually closed the 52 positions?

22. **V2: What is the regime transition buffer state history?** Was it always 2 (never decremented) or did manual resets occur?

23. **V2: What would happen if someone set `_ML_ENABLED = True`?** Would the system train on 43.7% fabricated data and immediately collapse?

---

## ADDENDUM 2: IF I HAD 8 HOURS TODAY

**V2 reprioritizes based on disconnected pipeline finding.**

### Hour 1: UNIFY SIGNAL PIPELINE (HIGHEST PRIORITY)
- **Files**: `main.py:6829-6878`, `tick_loop.py:787`
- **Deliverable**: Signal queue consumer calls `virtual_trader.open_position()` OR is removed
- **Accept**: Either:
  1. Wire consumer to execution with deduplication, OR
  2. Disable APScheduler signal paths and document TickLoop as sole execution path
- **Impact**: Restores 30+ sophisticated gates to production OR clarifies TickLoop is authoritative

### Hour 2: Kill the Silent Failures
- **Files**: `main.py:9222-9225,1295-1296`, `tick_loop.py:381-393`
- **Deliverable**: TickLoop failure → kill switch + Telegram alert. InvariantEnforcer init failure → FATAL.
- **Accept**: Mock TickLoop.start() to raise → kill switch activates within 60s. InvariantEnforcer init fail → system exits.

### Hour 3: InvariantEnforcer → ACTUAL ENFORCEMENT
- **Files**: `main.py:1329`, `invariant_enforcer.py:68`
- **Deliverable**: Schedule `_run_invariant_runtime_check()` every 60s. Call `enforce_boot()` at startup.
- **Accept**: Log shows "InvariantEnforcer: boot check passed" on startup. Log shows "Invariant check passed" every 60s.

### Hour 4: Data Age Check
- **Files**: `tick_loop.py:930-940`
- **Deliverable**: Reject data where last bar > 120s old during active session
- **Accept**: Mock stale yfinance → `_get_fresh_price()` returns 0.0. Log shows "Data age 180s exceeds limit, rejecting."

### Hour 5: Unify Confidence Floors + Weekly Halt + Max Positions
- **Files**: `circuit_breakers.py:120`, `risk_sizer.py:52,57,397`, `settings.yaml:623`, `tick_loop.py:286`, `daily_target.py:75`, `gate_diagnostics.py:62`, `universal_scanner.py:17`
- **Deliverable**: Confidence = 65. Weekly = 6%. Max positions = 3. SessionProtection = +2.5%.
- **Accept**: Boot with zero invariant failures. Grep returns zero standalone threshold literals.

### Hour 6: S16 ISA SHORT Guard + Transition Buffer Fix
- **Files**: `main.py:4841`, `universal_scanner.py`, `regime_classifier.py:500`
- **Deliverable**: S16 SHORTs rejected for ISA tickers. Transition buffer decrements.
- **Accept**: SHORT on QQQ3.L → rejected. Regime change → buffer counts to 0.

### Hour 7: Redis + ImmutableRiskRules
- **Files**: `docker-compose.yml:98`, `risk_sizer.py:31-69`
- **Deliverable**: Redis volatile-lru. ImmutableRiskRules raises on class-level mutation.
- **Accept**: Redis CONFIG GET = volatile-lru. `ImmutableRiskRules.RISK_PER_TRADE = X` → AttributeError.

### Hour 8: Deploy + Verify + Document
- **Files**: All modified files, `deploy.sh`, `tasks/AUDIT_REPORT_V2.md`
- **Deliverable**: System running on EC2 with all fixes. First tick produces signals with correct thresholds. Document changes.
- **Steps**:
  1. `pytest` → all pass
  2. `python -c "import main"` → InvariantEnforcer boot check passes
  3. Deploy to EC2
  4. Verify docker logs show InvariantEnforcer running, TickLoop alive, signal pipeline unified
  5. Wait for one tick cycle → verify positions can open
  6. Update `AUDIT_REPORT_V2.md` with fixes applied
  7. Commit

**End state**:
- Signal pipeline unified (APScheduler wired to execution OR disabled)
- InvariantEnforcer actually enforcing (12 invariants checked at boot + 60s)
- TickLoop crash-loud on failure
- Data age checked (no 15-min-old entries)
- All thresholds unified (confidence=65, weekly=6%, max_positions=3)
- S16 ISA-safe
- Transition buffer working
- Redis won't brick
- ImmutableRiskRules actually immutable

The system now loses trades for the RIGHT reasons (bad signals, bad timing, bad risk management) not WRONG reasons (disconnected pipelines, silent failures, contradictory thresholds, stale data, dead safety nets, bricked Redis).

The 100-trade validation gate can begin.

---

**END OF AUDIT REPORT V2**

*Generated: 2026-03-08 by NZT-48/AEGIS Full-Spectrum Institutional Audit V2*
*Auditor: Claude Opus 4.6*
*Scope: Deep-dive corrected & extended edition*
*Changes from V1: 5 V1 corrections, 4 new Tier-1 findings, 22 threshold contradictions (was 6), 8 new truth table rows, 8 new stop-ship items, 4 new sprint cards, 3 new plan patches, 5 new MV checklist items, 10 new critical questions*
*Key V2 findings: Disconnected pipelines, InvariantEnforcer dead code, 0/8 learning components influence decisions, overnight kill scope violation, transition buffer never called*
