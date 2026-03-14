# R24: PHASE 5 -- IMPLEMENTATION-READY OUTPUTS

**Date**: 2026-03-06
**Auditor**: Claude Opus 4.6
**Sources**: R22 (Triage), R23 (Four-Persona Audit), R17, R21
**Purpose**: Actionable implementation plan for the 10 P0 items, the 10 highest-impact quick fixes, and a binary Go-Live gate checklist.

---

## PART A: MVHF (Minimum Viable Hedge Fund) 22-HOUR P0 SPRINT

Items ordered by dependency (what must come first).

---

### P0-1: ISA Eligibility Gate (Existential Risk Prevention)
- **R22 ID**: R21-19
- **Files**: Create `qualification/isa_gate.py` (new module). Wire into `qualification/qualifier.py` (Stage 7, around line 596). Add ISA whitelist to `config/settings.yaml`.
- **LOC estimate**: ~150 lines new code + ~20 lines wiring in qualifier.py + ~15 lines in settings.yaml
- **Dependencies**: None -- this is the first item because it is existential. No other P0 blocks this.
- **Test plan**:
  - Unit test: `test_isa_gate.py` with 3 cases: (1) whitelisted ticker passes, (2) non-ISA ticker is BLOCKED with logged reason, (3) empty/malformed ticker is BLOCKED.
  - Integration test: Submit a signal for "AAPL" (non-ISA) through the qualifier pipeline and verify it is rejected before reaching the execution engine.
  - Regression test: Verify all 12 active ISA tickers (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L) pass the gate.
- **Risk if skipped**: One non-ISA trade voids the entire tax-free wrapper retroactively. HMRC crystallizes CGT on ALL prior gains. Existential financial risk.
- **Implementation sketch**:
  ```
  # qualification/isa_gate.py
  ISA_WHITELIST = set(cfg.get("isa_universe", {}).get("tickers", []))

  class ISAGate:
      def check(self, ticker: str) -> tuple[bool, str]:
          if ticker not in ISA_WHITELIST:
              return False, f"BLOCKED: {ticker} not in ISA whitelist"
          return True, "ISA eligible"

  # Wire into qualifier.py BEFORE any other qualification stage
  # This is the first gate -- if it fails, nothing else runs
  ```
- **Estimated time**: 8h (including Three-Key Safe architecture from GPT-14: file-based whitelist + code-level check + config-level check, all three must agree)

---

### P0-2: VIX/Regime Fail-OPEN to Fail-CLOSED
- **R22 ID**: R21-42
- **Files**: `feeds/market_structure.py` (line 489-496, `_default_vix()` method)
- **LOC estimate**: 2 lines changed
- **Dependencies**: None
- **Test plan**:
  - Unit test: Call `_default_vix()` and assert `vix == 99.0` and `risk_level == "RISK_OFF"`.
  - Integration test: Simulate yfinance failure (mock), verify the system enters RISK_OFF mode (no new trades) rather than NORMAL mode.
- **Risk if skipped**: VIX data outage causes system to trade in the most permissive mode (VIX=0.0, NORMAL) instead of halting. Trading blind during a VIX outage is a ruin vector.
- **Implementation sketch**:
  ```
  @staticmethod
  def _default_vix() -> dict[str, Any]:
      return {
          "vix": 99.0,        # was 0.0 -- fail-CLOSED
          "risk_level": "RISK_OFF",  # was "NORMAL"
          ...
      }
  ```
- **Estimated time**: 0.5h

---

### P0-3: asyncio.QueueFull Exception Mismatch (4 Sites)
- **R22 ID**: R21-06
- **Files**: `main.py` (lines 3081, 4208, 4437), `command_center/tick_loop.py` (line 1492)
- **LOC estimate**: 4 lines changed (one per site: `asyncio.QueueFull` -> `queue.Full`). Plus 1 import line if `queue` not already imported.
- **Dependencies**: None
- **Test plan**:
  - Unit test: Create a `Queue(maxsize=1)`, fill it, attempt `put_nowait()`, verify `queue.Full` is raised and caught.
  - Integration test: Set queue maxsize=1 in test config, push 2 signals, verify no crash and second signal is logged as dropped.
- **Risk if skipped**: When signal queue reaches maxsize=50 (inevitable over time since no consumer exists), the scan cycle crashes with an unhandled exception. Open positions go unmanaged until the next Docker restart. This is a direct ruin vector.
- **Implementation sketch**:
  ```
  # At each of the 4 sites, change:
  #   except asyncio.QueueFull:
  # to:
  #   except queue.Full:
  # Also add 'import queue' if not present, or use 'from queue import Full'
  ```
- **Estimated time**: 0.5h

---

### P0-4: Signal List Mutation During Iteration
- **R22 ID**: R21-04
- **Files**: Identify all `for sig in signals:` patterns where `signals` is modified during iteration (main.py and potentially other files).
- **LOC estimate**: ~5 lines changed (iterate over `signals[:]` or use list comprehension)
- **Dependencies**: None
- **Test plan**:
  - Unit test: Create a list of 5 mock signals, iterate with the fixed pattern, remove 2 during iteration, verify all 5 were visited.
  - Regression test: Run a full scan cycle with 5+ signals and verify none are skipped.
- **Risk if skipped**: Modifying a list during iteration skips elements in Python. Skipped signals could include flatten-all commands from circuit breakers -- meaning risk events are silently ignored.
- **Implementation sketch**:
  ```
  # Change:    for sig in signals:
  # To:        for sig in signals[:]:     (iterate over copy)
  # OR:        for sig in list(signals):  (explicit copy)
  # In all locations where signals list is mutated inside the loop
  ```
- **Estimated time**: 0.5h

---

### P0-5: ImmutableRiskRules __setattr__ Guard
- **R22 ID**: R21-12
- **Files**: `qualification/risk_sizer.py` (lines 30-59, `ImmutableRiskRules` class)
- **LOC estimate**: ~8 lines added
- **Dependencies**: None
- **Test plan**:
  - Unit test: Instantiate `ImmutableRiskRules()`, attempt `rules.RISK_PER_TRADE = 0.05`, verify `AttributeError` is raised.
  - Unit test: Verify all 17 rule values are accessible and correct after instantiation.
  - Regression test: Run the full qualification pipeline to ensure no existing code path mutates these values.
- **Risk if skipped**: Any bug or code path that accidentally modifies MAX_RISK_PCT from 0.0075 to 0.075 (a one-character typo) allows 10x the intended risk per trade. The "immutable" label provides false assurance.
- **Implementation sketch**:
  ```
  class ImmutableRiskRules:
      ...
      def __init__(self):
          self._rules_locked = True

      def __setattr__(self, name, value):
          if hasattr(self, '_rules_locked') and self._rules_locked:
              raise AttributeError(
                  f"CONSTITUTIONAL VIOLATION: Cannot modify {name}. "
                  f"ImmutableRiskRules are locked after initialization."
              )
          super().__setattr__(name, value)
  ```
- **Estimated time**: 0.5h

---

### P0-6: SessionProtection Verify Code = +2.0%, Clean Plan References
- **R22 ID**: R21-01
- **Files**: `config/settings.yaml` (line 604), plan document (all SessionProtection references)
- **LOC estimate**: 0 code lines (verification only). ~10 plan text edits.
- **Dependencies**: None
- **Test plan**:
  - Verification: Read `config/settings.yaml:600-608` and confirm the +2.0% threshold is in place. CONFIRMED: line 604 shows `pnl: [1.5, 2.0]` with action "STOP. Lock it in." and line 605 shows `pnl_above: 2.0` with action "DEFINITELY stop." The session protection STOPS at 1.5-2.0%, not at 1.5%. This means the system CAN reach 2.0% before being halted.
  - Plan fix: Update all plan references from +1.5% to match the actual config behavior.
- **Risk if skipped**: If plan says +1.5% and someone "fixes" the code to match the plan, the system literally cannot achieve its daily target. The 353x terminal wealth difference (R21 Q100) between 1.5% and 2.0% makes this the most expensive single-number error in the system.
- **Implementation sketch**: This is a verification + plan-text fix. No code changes needed if settings.yaml already shows +2.0%. Verify the code that reads this config interprets it correctly.
- **Estimated time**: 1h

---

### P0-7: Wire Transition Buffer + Add VIX 5% Hysteresis Deadband
- **R22 ID**: R21-13 + R21-14
- **Files**: `feeds/regime_classifier.py` (lines 47, 185, 293-298). Caller must be added wherever regime evaluation occurs (likely `main.py` or `feeds/regime_classifier.py:classify()` method).
- **LOC estimate**: ~40 lines new code (wiring + hysteresis logic) + ~10 lines config in settings.yaml
- **Dependencies**: None, but should be tested after P0-2 (fail-closed VIX default)
- **Test plan**:
  - Unit test: Simulate VIX oscillating at 25.0 +/- 0.5 for 20 ticks. With 5% deadband (entry at 26.25, exit at 23.75), verify ZERO regime changes occur.
  - Unit test: Simulate VIX jumping from 20 to 35. Verify regime changes to RISK_OFF after the transition buffer (1 session confirmation).
  - Integration test: Run scan cycle with VIX at boundary. Verify no regime flapping in logs.
- **Risk if skipped**: VIX oscillating at any threshold boundary causes 10-20 regime changes per day. Each change triggers a flatten-all + re-enter cycle, costing 40bps spread per flip. At 10 flips/day: 400bps/day = the system's entire expected daily profit is consumed by spread costs.
- **Implementation sketch**:
  ```
  # In regime_classifier.py, add hysteresis check:
  DEADBAND_PCT = 0.05  # 5% proportional deadband

  def _should_transition(self, new_regime, trigger_value, threshold):
      upper = threshold * (1 + DEADBAND_PCT)
      lower = threshold * (1 - DEADBAND_PCT)
      if self.current_regime == new_regime:
          return False  # already in this regime
      if trigger_value > upper:
          return True   # clearly above threshold
      if trigger_value < lower:
          return True   # clearly below threshold
      return False      # in deadband -- no change

  # Wire decrement_transition_buffer() call into the scan cycle
  # Call it once per scan cycle / once per session depending on resolution
  ```
- **Estimated time**: 3h

---

### P0-8: Circuit Breaker State Persistence to SQLite
- **R22 ID**: R21-16
- **Files**: `qualification/circuit_breakers.py` (new persistence layer). `delivery/database.py` (add circuit_breaker_state table).
- **LOC estimate**: ~80 lines (SQLite table creation, save/load methods, startup recovery)
- **Dependencies**: P0-2 (fail-closed default means startup without state defaults to safe mode)
- **Test plan**:
  - Unit test: Trigger L2 halt, persist to SQLite, create new CircuitBreakerSystem instance, verify L2 is still active on load.
  - Integration test: Simulate Docker restart (create new instance from persisted state), verify halt state survives.
  - Edge case: Verify that a persisted halt from yesterday does not carry over to today (daily halts are per-session, weekly halts persist across sessions).
- **Risk if skipped**: Operator can restart Docker to bypass any circuit breaker. Each restart cycle allows accumulating another full daily loss limit. With 3 restarts: 3 * 4% (L3) = 12% daily loss, far exceeding the Constitutional -8% weekly halt.
- **Implementation sketch**:
  ```
  # In circuit_breakers.py, add:
  class CircuitBreakerPersistence:
      def __init__(self, db_path="data/nzt48.db"):
          self.conn = sqlite3.connect(db_path)
          self._ensure_table()

      def _ensure_table(self):
          self.conn.execute("""
              CREATE TABLE IF NOT EXISTS circuit_breaker_state (
                  date TEXT, level TEXT, triggered_at TEXT,
                  daily_pnl_pct REAL, reason TEXT
              )""")

      def save_state(self, level, daily_pnl_pct, reason):
          ...

      def load_today_state(self) -> Optional[str]:
          # Returns highest halt level triggered today
          ...

  # On startup, CircuitBreakerSystem loads persisted state
  # L1/L2/L3 daily halts only persist for current trading day
  # Weekly halt persists for current week
  # Monthly halt persists for current month
  ```
- **Estimated time**: 2h

---

### P0-9: Weekly -8% Halt + Monthly -15% Halt Implementation
- **R22 ID**: R21-18
- **Files**: `qualification/circuit_breakers.py` (add WeeklyHalt and MonthlyHalt classes). `config/settings.yaml` (add thresholds). Wire into `check_all()` method.
- **LOC estimate**: ~120 lines new code
- **Dependencies**: P0-8 (circuit breaker persistence -- weekly/monthly halts MUST persist across restarts)
- **Test plan**:
  - Unit test: Simulate 5 consecutive daily L3 events (5 * -4% = -20% weekly). Verify weekly halt triggers at -8%.
  - Unit test: Simulate monthly cumulative loss reaching -15%. Verify monthly halt triggers.
  - Unit test: Verify halt state persists in SQLite across restart.
  - Edge case: Verify weekly halt resets on Monday. Monthly halt resets on 1st of month.
- **Risk if skipped**: Without weekly halt, repeated daily L3 events compound: 5 days * -4% = -18.1% weekly drawdown. Without monthly halt, this can reach -30%+ before any automated intervention. The plan gives ~48% probability of a day triggering weekly halt during 63-day paper phase -- meaning this WILL be tested.
- **Implementation sketch**:
  ```
  # In circuit_breakers.py:
  WEEKLY_HALT_PCT = -0.08    # -8% weekly halt (Constitutional)
  MONTHLY_HALT_PCT = -0.15   # -15% monthly halt (Constitutional)

  def check_weekly_halt(self, weekly_pnl_pct: float) -> dict:
      if weekly_pnl_pct <= WEEKLY_HALT_PCT:
          return {
              "halt": True,
              "level": "WEEKLY_HALT",
              "action": "FLATTEN_ALL_HALT_UNTIL_MANUAL_REVIEW",
          }
      return {"halt": False}

  # Similar for monthly. Integrate into check_all() as an additional
  # breaker that overrides all others if triggered.
  # Weekly P&L = sum of daily P&L since Monday
  # Monthly P&L = sum of daily P&L since 1st of month
  ```
- **Estimated time**: 3h

---

### P0-10: ISA Correlation Families -- Add .L Ticker Mappings
- **R22 ID**: R21-03
- **Files**: `qualification/portfolio_risk.py` (lines 94-104, `ISA_FACTOR_GROUPS` already exists). Wire concentration check into qualifier pipeline if not already wired.
- **LOC estimate**: ~30 lines (tighten max-per-group from 3 to 2 for nasdaq_beta_long, wire into pre-trade check if not already)
- **Dependencies**: P0-1 (ISA gate must exist first -- correlation check operates within the ISA universe)
- **Test plan**:
  - Unit test: Propose 3 nasdaq_beta_long tickers (QQQ3.L, 3LUS.L, SP5L.L) simultaneously. Verify the 3rd is BLOCKED.
  - Unit test: Propose 1 nasdaq_beta_long + 1 semiconductors_lev. Verify both pass (different groups).
  - Integration test: Run full qualifier with max-per-group=2 and verify enforcement.
- **Risk if skipped**: 3 concurrent positions from nasdaq_beta_long gives effective independent positions = 1.11 (correlation rho > 0.85). A -4% NASDAQ day produces simultaneous -12% on all 3 positions = -9% portfolio loss. This exceeds L3 and weekly halt simultaneously. The portfolio behaves as a single leveraged NASDAQ bet.
- **Implementation sketch**:
  ```
  # In portfolio_risk.py, tighten ISA_FACTOR_GROUPS max-per-group:
  ISA_MAX_PER_GROUP = {
      "nasdaq_beta_long": 2,     # was effectively 3
      "semiconductors_lev": 2,   # max 2 semi plays
      "default": 2,              # default for all groups
  }

  # Ensure check_isa_concentration() is called in qualifier.py
  # BEFORE position sizing, as a hard gate.
  ```
- **Estimated time**: 3h

---

### DEPENDENCY GRAPH

```
P0-1 (ISA Gate)          -- no deps, highest priority, start here
P0-2 (Fail-Closed VIX)   -- no deps, can parallel with P0-1
P0-3 (QueueFull fix)      -- no deps, can parallel
P0-4 (List mutation)      -- no deps, can parallel
P0-5 (Immutable guard)    -- no deps, can parallel
P0-6 (SessionProtection)  -- no deps, can parallel (verification only)
P0-7 (Hysteresis)         -- benefits from P0-2 first (VIX defaults)
P0-8 (CB persistence)     -- benefits from P0-2 first
P0-9 (Weekly/Monthly)     -- REQUIRES P0-8 (persistence layer must exist)
P0-10 (Correlation)       -- benefits from P0-1 first (ISA gate context)
```

**Recommended execution order**: P0-2, P0-3, P0-4, P0-5, P0-6 (parallel, ~3h total) -> P0-1 (8h) -> P0-7, P0-8 (parallel, ~5h) -> P0-9 (3h, needs P0-8) -> P0-10 (3h)

**Total estimated time: ~22 hours**

---

## PART B: "IF I HAD 8 HOURS TODAY" -- The 10 Highest-Impact Code Changes

Ordered by impact-per-hour (risk reduction per hour invested). Ruin-prevention fixes first.

---

### Fix 1: VIX Fail-OPEN to Fail-CLOSED -- 0.25h
- **File(s)**: `feeds/market_structure.py` (line 489-496)
- **Current behavior**: `_default_vix()` returns `vix: 0.0, risk_level: "NORMAL"`. VIX data outage = system trades with maximum permissiveness.
- **Fix**: Change `vix` from `0.0` to `99.0`. Change `risk_level` from `"NORMAL"` to `"RISK_OFF"`. Two lines.
- **Lines to change**: 2
- **Test**: Call `_default_vix()`, assert vix == 99.0 and risk_level == "RISK_OFF". Simulate yfinance failure, verify no new trades.
- **Risk class**: RUIN

---

### Fix 2: asyncio.QueueFull Exception Mismatch -- 0.25h
- **File(s)**: `main.py` (lines 3081, 4208, 4437), `command_center/tick_loop.py` (line 1492)
- **Current behavior**: Catches `asyncio.QueueFull` but queue is stdlib `queue.Queue` (imported at main.py:23). When queue fills, `queue.Full` is raised but not caught. Unhandled exception crashes the scan cycle. Open positions go unmanaged.
- **Fix**: Replace `asyncio.QueueFull` with `queue.Full` at all 4 sites. Add `from queue import Full` if needed.
- **Lines to change**: 4 (plus 1 import if needed)
- **Test**: Fill queue to maxsize, push one more, verify exception is caught and logged.
- **Risk class**: RUIN (unmanaged positions during scan crash)

---

### Fix 3: ImmutableRiskRules __setattr__ Guard -- 0.5h
- **File(s)**: `qualification/risk_sizer.py` (lines 30-59)
- **Current behavior**: 17 "immutable" risk rules are plain class attributes with no mutation protection. Any code can modify RISK_PER_TRADE, MAX_DAILY_LOSS, etc. at runtime.
- **Fix**: Add `__setattr__` override that raises `AttributeError` after initialization. The `_rules_locked` flag at line 59 already exists but is never checked.
- **Lines to change**: ~8 lines added
- **Test**: `rules = ImmutableRiskRules(); rules.RISK_PER_TRADE = 0.05` must raise AttributeError.
- **Risk class**: RUIN (accidental 10x risk amplification)

---

### Fix 4: Signal List Mutation During Iteration -- 0.25h
- **File(s)**: `main.py` (locate all `for sig in signals:` patterns), `dashboard/api.py:268`
- **Current behavior**: Modifying `signals` list while iterating skips elements. Skipped elements could be flatten-all commands from circuit breakers.
- **Fix**: Replace `for sig in signals:` with `for sig in signals[:]:` (iterate over copy) at all mutation sites.
- **Lines to change**: ~3-5 lines
- **Test**: Create list of 5 signals, remove 2 during iteration, verify all 5 were visited.
- **Risk class**: RUIN (missed circuit breaker flatten commands)

---

### Fix 5: Wire Transition Buffer (Prevent Regime Flapping) -- 1.5h
- **File(s)**: `feeds/regime_classifier.py` (lines 47, 185, 293-298)
- **Current behavior**: `decrement_transition_buffer()` is defined but never called. Regime changes are instant with no confirmation. VIX oscillating at any threshold causes 10-20 regime changes/day, each costing 40bps spread.
- **Fix**: Call `decrement_transition_buffer()` once per scan cycle. Add VIX hysteresis deadband (5% proportional). Requires adding the call site and the deadband logic.
- **Lines to change**: ~40 lines
- **Test**: Simulate VIX at 25.0 +/- 0.5 for 20 ticks, verify zero regime changes with deadband active.
- **Risk class**: PROFITABILITY (400-800bps/day erosion from flapping)

---

### Fix 6: Circuit Breaker Persistence to SQLite -- 2h
- **File(s)**: `qualification/circuit_breakers.py` (new persistence methods), `delivery/database.py` (new table)
- **Current behavior**: All circuit breaker state (L1/L2/L3 halts) is in-memory only. Docker restart clears all halts. Operator can restart to bypass any halt, accumulating unlimited losses.
- **Fix**: Add SQLite table `circuit_breaker_state`. Save halt events on trigger. Load on startup. Daily halts expire at session end. Weekly/monthly halts persist.
- **Lines to change**: ~80 lines
- **Test**: Trigger L2, persist, create new instance, verify L2 is still active.
- **Risk class**: RUIN (unlimited loss via restart bypass)

---

### Fix 7: Weekly -8% and Monthly -15% Halts -- 1.5h
- **File(s)**: `qualification/circuit_breakers.py`
- **Current behavior**: Weekly loss tracking exists at -6% WARNING level (settings.yaml:621) but no -8% HALT. Monthly -15% entirely missing. No code checks weekly or monthly cumulative P&L for halt purposes.
- **Fix**: Add weekly/monthly P&L accumulators. Add halt check in `check_all()`. Persist to SQLite (requires Fix 6).
- **Lines to change**: ~80 lines
- **Test**: Simulate 5 daily L3 events, verify weekly halt triggers at cumulative -8%.
- **Risk class**: RUIN (unbounded multi-day losses)

---

### Fix 8: ML Regime Map Fix -- 0.5h
- **File(s)**: `core/ml_meta_model.py` (line 48)
- **Current behavior**: `_REGIME_MAP` contains keys ("bull", "bear", "neutral", etc.) that do not match ANY actual regime output ("TRENDING_UP_STRONG", "RANGE_BOUND", etc.). `_encode_regime()` returns -1 for all real regimes. All ML regime features are permanently -1.
- **Fix**: Update `_REGIME_MAP` keys to match actual regime classifier output strings. Map all 8 regime states to unique integers.
- **Lines to change**: ~10 lines
- **Test**: For each of the 8 actual regime strings, verify `_encode_regime()` returns a unique non-negative integer.
- **Risk class**: PROFITABILITY (ML model is blind to regime, all training data contaminated)

---

### Fix 9: ISA Gate Skeleton (Minimal) -- 1h
- **File(s)**: Create `qualification/isa_gate.py`. Wire into `qualification/qualifier.py`.
- **Current behavior**: No validation that a proposed trade is ISA-eligible. Non-ISA trade voids entire tax wrapper retroactively.
- **Fix**: Create minimal whitelist-based gate. 12 tickers hardcoded + config-driven. BLOCK anything not on list. Wire as the FIRST qualification stage (before all other checks).
- **Lines to change**: ~50 lines (minimal version; full Three-Key Safe is 150 lines)
- **Test**: Submit "AAPL" signal, verify blocked. Submit "QQQ3.L", verify passed.
- **Risk class**: RUIN (existential -- entire ISA tax benefit voided)

---

### Fix 10: R5 Overnight Kill for All ETPs -- 0.25h
- **File(s)**: `config/settings.yaml` (overnight_kill settings per ticker)
- **Current behavior**: Overnight kill only enforced for 5x products. 3x ETPs can be held overnight, exposing to gap risk (-9% on 3x ETP from -3% NASDAQ gap).
- **Fix**: Set overnight_kill=True for all 12 active ISA tickers during paper/limited live phases. One config line per ticker.
- **Lines to change**: ~12 lines in settings.yaml
- **Test**: Verify at 16:25 UK that the system attempts to close all ETP positions, including 3x products.
- **Risk class**: RUIN (overnight gap loss on leveraged ETPs)

---

### 8-HOUR SPRINT SUMMARY

| Fix | Time | Risk Class | Cumulative Hours |
|-----|------|------------|-----------------|
| Fix 1: VIX Fail-Closed | 0.25h | RUIN | 0.25h |
| Fix 2: QueueFull exception | 0.25h | RUIN | 0.5h |
| Fix 3: Immutable guard | 0.5h | RUIN | 1.0h |
| Fix 4: List mutation | 0.25h | RUIN | 1.25h |
| Fix 9: ISA gate (minimal) | 1.0h | RUIN | 2.25h |
| Fix 10: Overnight kill | 0.25h | RUIN | 2.5h |
| Fix 5: Transition buffer | 1.5h | PROFITABILITY | 4.0h |
| Fix 6: CB persistence | 2.0h | RUIN | 6.0h |
| Fix 7: Weekly/Monthly halts | 1.5h | RUIN | 7.5h |
| Fix 8: ML regime map | 0.5h | PROFITABILITY | 8.0h |

**First 2.5 hours eliminate 6 RUIN-class bugs. Remaining 5.5 hours add structural risk protection.**

---

## PART C: GO-LIVE GATE CHECKLIST

All 20 items must be YES before deploying with real capital. No exceptions.

---

### EXISTENTIAL GATES (1-5)

- [ ] **G1: ISA eligibility gate implemented and tested.** Every trade passes through ISA whitelist validation. Non-ISA ticker = hard BLOCK. Three-Key Safe architecture verified (config + code + file all agree).

- [ ] **G2: SessionProtection threshold at +2.0% verified in both code and config.** `config/settings.yaml` session_protection daily rules confirm the system can reach +2.0% before halting. All plan references updated to match.

- [ ] **G3: ImmutableRiskRules are actually immutable.** `__setattr__` guard raises `AttributeError` on any modification attempt after initialization. Test proves RISK_PER_TRADE cannot be changed at runtime.

- [ ] **G4: VIX/regime default is fail-CLOSED.** `_default_vix()` returns vix=99.0, risk_level="RISK_OFF". Verified by test simulating yfinance outage.

- [ ] **G5: Kill switch behavior fully specified and tested.** Kill switch file: (1) read on every scan cycle, (2) flattens ALL positions, (3) halts all new entries, (4) persists across Docker restart, (5) only removable by operator deleting file + confirming.

---

### CIRCUIT BREAKER GATES (6-10)

- [ ] **G6: Daily L1/L2/L3 circuit breakers tested.** L1 (-1.5%) reduces size 50%. L2 (-2.5%) stops new entries. L3 (-4.0%) flattens all and halts. All 3 levels verified with simulated P&L.

- [ ] **G7: Weekly -8% halt implemented and tested.** Cumulative weekly P&L tracked from Monday. At -8%, system halts and flattens all. Persists across restart.

- [ ] **G8: Monthly -15% halt implemented and tested.** Cumulative monthly P&L tracked from 1st of month. At -15%, system halts and flattens all. Persists across restart.

- [ ] **G9: Circuit breaker state persists to SQLite.** L1/L2/L3/weekly/monthly halt states survive Docker restart. Verified by restart test.

- [ ] **G10: No restart-bypass of circuit breakers.** After triggering L3, restart the Docker container, verify L3 is still enforced.

---

### POSITION MANAGEMENT GATES (11-15)

- [ ] **G11: ISA correlation families enforce max-per-group = 2.** Attempting 3 positions from nasdaq_beta_long group is blocked. Verified with test.

- [ ] **G12: R5 overnight kill enforced for ALL leveraged ETPs (3x and 5x).** At 16:25 UK, all ETP positions are closed. No overnight hold on any leveraged product during paper/limited live.

- [ ] **G13: Signal queue exception handling correct.** `queue.Full` (not `asyncio.QueueFull`) is caught at all 4 sites. Queue overflow produces log warning, not crash.

- [ ] **G14: Signal list iteration safe from mutation.** All `for sig in signals:` loops iterate over a copy (`signals[:]`) where the list is modified during iteration.

- [ ] **G15: Single Risk Arbiter prevents double-flatten.** All 12+ flatten paths route through a single executor. Concurrent flatten calls cannot produce SHORT exposure.

---

### DATA AND REGIME GATES (16-18)

- [ ] **G16: VIX hysteresis deadband (5%) prevents regime flapping.** VIX oscillating at threshold +/- 1 point does NOT cause regime changes. Verified with 20-tick boundary simulation.

- [ ] **G17: Transition buffer wired and functional.** `decrement_transition_buffer()` is called on every scan cycle. Regime changes require N-session confirmation.

- [ ] **G18: Stale data detection implemented.** Tick-change counter detects 5+ unchanged price readings as suspicious. RVOL=0 check active. Cross-reference with underlying index (e.g., QQQ for QQQ3.L) implemented.

---

### OPERATIONAL GATES (19-20)

- [ ] **G19: 63+ paper trades completed with validated metrics.** Minimum 63 paper trades executed. Win rate, average win, average loss, max drawdown, and rung reach probabilities all recorded. Shadow markout data collected for CQO-01 validation.

- [ ] **G20: Failure simulation drills passed.** All 6 drills completed: (1) kill switch activation + recovery, (2) network failure during open position, (3) VIX data outage, (4) Docker crash during trade execution, (5) circuit breaker cascade L1->L2->L3, (6) weekly halt trigger + manual review + restart.

---

### CHECKLIST SCORING

- **20/20 YES**: Cleared for limited live trading (Phase B: 10% equity, 1 position max)
- **19/20 YES**: Review the failing item. If it is G1-G5 (existential): NO-GO. If G6-G20: CRO discretion.
- **<19/20 YES**: NO-GO. Return to development.

---

*R24 Implementation Sprint Plan Complete. 10 P0 items specified with pseudocode. 10 quick fixes ordered by impact-per-hour. 20-item Go-Live gate checklist defined.*

**Prepared by:** Claude Opus 4.6
**Date:** 2026-03-06
**Classification:** INTERNAL -- NZT-48 Adversarial Review Phase 5
