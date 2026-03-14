# AEGIS V14 Implementation Plan — Full System Adversarial Audit
## NZT-48 Momentum-Volatility Intelligence Engine
### Generated: 2026-03-08 | 8-Agent Deep Audit | ~120 Findings Consolidated

---

## EXECUTIVE SUMMARY

8 parallel adversarial audit agents analysed every file in the NZT-48 system. They found **22 CRITICAL (P0)**, **34 HIGH (P1)**, **40 MEDIUM (P2)**, and **~25 LOW (P3)** issues across 7 subsystems. The system **cannot trade safely in its current state** — multiple undefined methods will crash at runtime, the 5x ETP hard kill is completely non-functional, the position sizer calls methods that don't exist, and several safety features exist only as constants/state without any logic.

---

## PHASE 0: SYSTEM-KILLING CRASHES (Must fix before any trade)
**Estimated: 4-6 hours | Blocks everything**

### 0-01: DynamicSizer calls undefined methods → instant crash
- **File**: `qualification/dynamic_sizer.py` lines 345-356
- **Bug**: `_check_deployment_cap()` and `_check_portfolio_cvar_gate()` called but never defined
- **Impact**: Every sizing call crashes with AttributeError. No signal can ever be sized.
- **Fix**: Implement both methods or remove calls with TODO stubs

### 0-02: daily_target.py calls undefined `_get_gap_to_adr_ratio()` and `_get_premarket_bias()`
- **File**: `strategies/daily_target.py`
- **Bug**: Every SLOW-tier signal evaluation crashes
- **Impact**: SLOW tier (the primary scanning path) is broken
- **Fix**: Implement methods or stub with defaults

### 0-03: `Signal.metadata` AttributeError
- **File**: `strategies/daily_target.py` line 808
- **Bug**: `signal.metadata = signal.metadata or {}` — Signal has no `metadata` attribute
- **Impact**: First successful signal creation crashes
- **Fix**: Add `metadata: dict = field(default_factory=dict)` to Signal dataclass

### 0-04: `run_scan()` returns None → startup crash
- **File**: `main.py` lines 1688-1692
- **Bug**: Bare `return` instead of `return []` on off-hours/weekends
- **Impact**: `len(None)` TypeError on every startup outside market hours
- **Fix**: `return []` on both early-return paths

### 0-05: `_update_state_from_db()` missing required `conn` argument
- **File**: `main.py` line 7467
- **Bug**: Called without `conn` parameter → TypeError
- **Impact**: Daily reset fails, all next-day risk limits use stale data
- **Fix**: Wrap in `with transaction() as conn:`

### 0-06: `get_daily_pnl()` / `get_consecutive_losses()` missing `conn`
- **File**: `main.py` lines 7593-7594
- **Bug**: Same pattern as 0-05
- **Impact**: Watchdog artifacts fail to write
- **Fix**: Same pattern fix

### 0-07: `_force_close_5x_etps()` iterates by position_id but checks as if ticker
- **File**: `main.py` lines 7211-7216
- **Bug**: `open_positions` is keyed by UUID, not ticker. Also missing `exit_price` arg.
- **Impact**: A-12 5x hard kill at 15:30 is 100% non-functional. 5x positions survive overnight.
- **Fix**: Iterate by pos_id, check `pos.ticker`, pass exit_price

### 0-08: Exit Engine `close_position` passes ticker instead of position_id
- **File**: `main.py` lines 6988-6993
- **Bug**: `close_position(ticker, ...)` but API expects `position_id`
- **Impact**: EXIT_NOW force-close is broken — positions flagged for exit never close
- **Fix**: Look up position_id from ticker first

### 0-09: `_api_pusher.push_signal()` method doesn't exist
- **File**: `main.py` line 6839
- **Bug**: `push_signal` never defined on `_APIPusher`, silently swallowed by `except: pass`
- **Impact**: Zero signals ever reach dashboard
- **Fix**: Implement `push_signal()` method

---

## PHASE 1: SAFETY SYSTEM FAILURES (Critical risk controls broken)
**Estimated: 6-8 hours**

### 1-01: Chandelier `_run_chandelier_ladder()` undefined → profit ladder dead
- **File**: `execution/virtual_trader.py` line 1649
- **Bug**: VirtualTrader calls undefined method, silently swallowed by try/except
- **Impact**: Profit trailing via VT path fails silently. Only main.py loop saves it.
- **Fix**: Remove dead delegation code or implement method

### 1-02: Chandelier Redis TTL 24h deletes active trade state
- **File**: `core/chandelier_exit.py` line 135
- **Bug**: `ex=86400` means >24h container outage wipes all trailing stops
- **Impact**: Post-restart, trailing stops reset to entry price. Profits surrendered.
- **Fix**: Remove TTL or extend to 7 days

### 1-03: No market hours guard on chandelier exit
- **File**: `main.py` reconciliation loop (line 6852+)
- **Bug**: Chandelier fires on stale post-close data
- **Impact**: False exit signals after 16:30 UK
- **Fix**: Add `if not is_lse_open(): return` to reconciliation

### 1-04: `record_stopout()` never called → anti-cascade is dead code
- **File**: `qualification/circuit_breakers.py`
- **Bug**: A-09 anti-cascade exists but `record_stopout()` is never invoked from VT or main.py
- **Impact**: 3-in-15-min stop-out cascade detection cannot function
- **Fix**: Wire `record_stopout()` into VirtualTrader.close_position() for STOP_HIT reasons

### 1-05: Circuit breaker size_multiplier never consumed by DynamicSizer
- **File**: `qualification/dynamic_sizer.py`
- **Bug**: CB returns size_multiplier (0.5 during drawdown) but sizer ignores it
- **Impact**: Full-size positions during active circuit breaker events
- **Fix**: Pass CB state to sizer, apply multiplier

### 1-06: Qualifier Stage 6 bypasses 0.75% constitutional cap
- **File**: `qualification/qualifier.py` lines 453-534
- **Bug**: Can escalate risk to 1.5% for high-confidence signals
- **Impact**: Positions 2x larger than constitutional limit
- **Fix**: Clamp at 0.75% unconditionally

### 1-07: Reconciler runs 24/7 with no market hours guard
- **File**: `main.py` lines 5641-5650
- **Bug**: 8,640 unnecessary reconciliations/day outside market hours
- **Impact**: API rate limits, stale price exits, CPU waste
- **Fix**: Guard with `is_lse_open()`

### 1-08: `load_history()` doesn't update `_total_trade_count`
- **File**: `qualification/dynamic_sizer.py` lines 823-857
- **Bug**: After loading 500 historical trades, adaptive Kelly starts from 0
- **Impact**: Permanently stuck at 1/8th Kelly instead of intended half-Kelly
- **Fix**: Add `self._total_trade_count = len(r_multiples)`

### 1-09: Duplicate Telegram notifications (2-3x per signal)
- **File**: `main.py` lines 3542, 4711, 6834
- **Bug**: Multiple delivery paths each send Telegram independently
- **Impact**: Alert fatigue, masked real alerts
- **Fix**: Single delivery point (queue consumer only)

### 1-10: `qualification_log` type mismatch (list treated as string)
- **File**: `main.py` lines 3633-3636
- **Bug**: `list + str` TypeError crashes AI signal enhancement
- **Impact**: No AI verdicts applied to any signal
- **Fix**: Use `signal.qualification_log.append()`

---

## PHASE 2: DEAD FEATURES (Exist in constants/state but have no logic)
**Estimated: 8-12 hours**

### 2-01: D-04 first half-hour predictability — records in unreachable window
### 2-02: D-05 rebalancing flow awareness — constants only, zero logic
### 2-03: D-06 no-signal escalation protocol — constants only, zero logic
### 2-04: G-05 EWMA vol — computed but never consumed for stop widening
### 2-05: G-07/G-08 shadow logging — called but output never used
### 2-06: Macro `get_size_multiplier()` — never called, macro doesn't affect sizing
### 2-07: HMM + Fear&Greed confidence adjustments — never added to total
### 2-08: F-15 cluster notional cap — stub always returns 0, no enforcement
### 2-09: Chandelier banking (bank_pct) — nobody consumes it, no partial close
### 2-10: `_portfolio_daily_returns` — declared but never populated
### 2-11: Reconciliation auditor — full skeleton, zero implementation
### 2-12: Power Hour boost — outside LSE gate, can never fire

---

## PHASE 3: LOGIC ERRORS & INCONSISTENCIES
**Estimated: 6-8 hours**

### 3-01: G-06 financing drag veto uses wrong expected return proxy (always vetoes)
### 3-02: SHOCK_RECOVERY decrements per signal eval, not per session
### 3-03: Regime enum values BREAKOUT/BREAKDOWN/CRASH/UNDEFINED don't exist
### 3-04: HIGH_VOLATILITY / RISK_OFF / REGIME_FLAPPING regimes unhandled
### 3-05: Gap scanner emits illegal SHORT signals in ISA universe
### 3-06: `_daily_signal_count` key type mismatch (str vs date) — cap bypassed
### 3-07: `_SPREAD_TOD_MULTIPLIERS` and `_SPREAD_VIX_MULTIPLIERS` specs diverge from code
### 3-08: VIX emergency veto blocks inverse ETPs (should be excluded)
### 3-09: Weekly/monthly DD uses current equity, not starting equity
### 3-10: Fear & Greed Index is CRYPTO, not equities
### 3-11: `^VXMT` deprecated on yfinance, defaults mask crashes
### 3-12: ETP overnight protection uses ET time, not UK time for LSE ETPs
### 3-13: `target_1r` never set for regular signals (always 0.0)
### 3-14: Stranger penalty asymptotes to win rate, not 1.0 (permanent under-sizing)
### 3-15: Consecutive loss Tier 2 dead code (same threshold as Tier 3)
### 3-16: Confidence + streak + ToD scalars compound above 1.0x (amplification in reduction pipeline)

---

## PHASE 4: INFRASTRUCTURE & CONFIG
**Estimated: 4-6 hours**

### 4-01: Redis single-instance noeviction — telemetry will fill and block all writes
### 4-02: Redis no Docker healthcheck — race condition on startup
### 4-03: Redis mem_limit (256MB) vs maxmemory (200MB) — OOM on AOF rewrite
### 4-04: IB Gateway port 4002 not verified in startup gate
### 4-05: settings.yaml GOOGL_LONG maps to Gold ETP (3GOL.L), not Alphabet
### 4-06: Confidence floor: settings.yaml=60, ThresholdRegistry=65, ImmutableRules=65
### 4-07: Phantom tickers in settings.yaml (60+ dead mappings)
### 4-08: SESSION_CONFIG timezone=UTC, rest of system uses Europe/London
### 4-09: Hardcoded Redis password in 3 files
### 4-10: No IB Gateway startup check or continuous heartbeat
### 4-11: Backup script does BGSAVE but only backs up AOF (dump.rdb ignored)
### 4-12: API port 8000 exposed without auth verification

---

## PHASE 5: MEMORY LEAKS & PERFORMANCE
**Estimated: 2-3 hours**

### 5-01: `_all_trades` list unbounded — grows forever
### 5-02: Duplicate close detection missing in update_prices (ETP_OVERNIGHT + REGIME_FLIP)
### 5-03: Unrealised P&L doesn't include financing drag (only at close)
### 5-04: ML meta-model features always use defaults (IndicatorSnapshot vs dict mismatch)
### 5-05: 5 separate Redis connections (should share pool)
### 5-06: Sync `time.sleep()` in data feed retry blocks event loop
### 5-07: `_heartbeat_loop` ignores kill switch

---

## CONSOLIDATED SEVERITY MATRIX

| Priority | Count | Description |
|----------|-------|-------------|
| **P0 CRITICAL** | 22 | System crashes, broken safety controls, non-functional features |
| **P1 HIGH** | 34 | Logic errors, dead safety features, incorrect calculations |
| **P2 MEDIUM** | 40 | Spec divergence, inconsistencies, performance issues |
| **P3 LOW** | ~25 | Code quality, dead imports, documentation |

### IMPLEMENTATION ORDER
1. **Phase 0** first — system literally cannot start/trade without these
2. **Phase 1** second — safety controls must work before any paper trading
3. **Phase 4** third — infrastructure must be stable
4. **Phase 2+3** can be parallelised after 0+1+4

### ESTIMATED TOTAL EFFORT
- Phase 0: **4-6 hours** (9 items, mostly small fixes)
- Phase 1: **6-8 hours** (10 items, wiring + logic)
- Phase 2: **8-12 hours** (12 items, new feature code)
- Phase 3: **6-8 hours** (16 items, logic corrections)
- Phase 4: **4-6 hours** (12 items, config + infra)
- Phase 5: **2-3 hours** (7 items, performance)
- **TOTAL: ~30-43 hours of implementation**

---

## VALIDATION GATE (after all phases)
Before any paper trading resumes:
1. `python3 -m py_compile` passes on ALL modified files
2. Every Phase 0 fix verified with targeted unit test
3. Full startup → 1-hour simulated market run → check logs for errors
4. All safety controls verified: CB halt, 5x kill, chandelier exit, anti-cascade
5. Redis state persistence verified across container restart
6. 200-Trade Validation Gate criteria: WR >= 40%, PF > 1.2, Sharpe > 1.0
