# AEGIS — Stop-Ship Register & Thresholds
> Extracted from AEGIS Master Plan v16.2. Source of truth for what's broken.
> See [README](README.md) for full index.
---

# SECTION 0: STOP-SHIP STATUS — READ THIS FIRST {#section-0}

**98 stop-ship items (40 P0 + 58 P1). ZERO fixed in code. This section must be updated as fixes land.**

**WARNING**: S15 has a **0% win rate across 52 paper trades** (playbook.json, 2026-02-28). The timing defects (T-01 through T-08) are the ROOT CAUSE. These must be fixed BEFORE any other P0 item. A system that enters every trade late will lose regardless of how perfect the risk architecture is.

### P0-CRITICAL (37 Items — was 36, +1 from Section 2H Runtime Invariant Contract)

| # | ID | Description | File(s) | Status | Est. Hours |
|---|-----|-------------|---------|--------|-----------|
| 1 | R21-19 | ISA eligibility gate — 100% MISSING. One non-ISA trade voids entire tax wrapper. File `uk_isa/isa_eligibility.py` DOES NOT EXIST. ISA universe data IS defined in `uk_isa/isa_universe.py` (`FROZEN_TICKERS` frozenset, `TICKER_REGISTRY`). Fix: create `isa_eligibility.py` with gate function `is_isa_eligible(ticker: str) -> bool` that checks `ticker in FROZEN_TICKERS`. Wire into BOTH `_execute_s15_priority_path` (main.py:3746) AND the general qualification gauntlet. Gate should run EARLY in sequence (before position sizing — fast reject). | `uk_isa/isa_eligibility.py` (CREATE), `uk_isa/isa_universe.py` (SOURCE) | OPEN | 8h |
| 2 | R21-01 | SessionProtection — verify code=+2.0%, clean all plan refs to +1.5% | `config/settings.yaml:604` | OPEN | 1h |
| 3 | R21-03 | Correlation families US-only — ISA .L tickers never match any family. `_FAMILIES` at dynamic_sizer.py:1302-1313 contains only US tickers (QQQ, SPY, NVDA, etc.). ISA .L tickers (QQQ3.L, QQQ5.L, QQQS.L, etc.) never match — correlation penalty NEVER fires for ISA portfolio. Fix: add ISA correlation families: `{"QQQ_linked": ["QQQ3.L", "QQQ5.L", "QQQS.L"], "SP_linked": ["3LUS.L", "3USS.L", "SP5L.L"], "SEMI_linked": ["NVD3.L", "3SEM.L", "TSM3.L", "MU2.L"], "TSLA_linked": ["TSL3.L"], "AI_linked": ["GPT3.L"]}`. | `qualification/dynamic_sizer.py:1302-1313` | OPEN | 2h |
| 4 | R21-04 | Signal list mutation during iteration — `raw_signals.remove(_sig)` inside `for _sig in raw_signals` loop at main.py:1929. Classic Python anti-pattern that can skip elements. Fix: change to list comprehension `raw_signals = [s for s in raw_signals if not ml_veto(s)]` OR iterate over a copy `for _sig in raw_signals[:]`. | `main.py:1929` | OPEN | 0.5h |
| 5 | R21-06 | `asyncio.QueueFull` exception mismatch — Queue is `queue.Queue` (stdlib threading queue, imported at main.py:23) but exception caught is `asyncio.QueueFull` (wrong module). Correct exception is `queue.Full`. Bug at lines 3081, 4208, and 4437. Falls through to outer `except Exception` handler (silent degradation, not crash). Fix: change `except asyncio.QueueFull` to `except queue.Full` at all three locations. | `main.py:23,3081,4208,4437` | OPEN | 0.5h |
| 6 | R21-42 | VIX/regime fail-OPEN — `_default_vix()` at market_structure.py:489-496 returns `{"vix": 0.0, "risk_level": "NORMAL"}`. Should be fail-CLOSED: change to `{"vix": 99.0, "risk_level": "EXTREME"}`. Also set `vix3m` default to 99.0 and `term_structure` default to `"backwardation"` (conservative). | `feeds/market_structure.py:489-496` | OPEN | 0.5h |
| 7 | R21-12 | ImmutableRiskRules fully mutable — `_rules_locked = True` set at risk_sizer.py:59 but NEVER CHECKED. No `__setattr__`, `__delattr__`, metaclass, `__slots__`, or `@dataclass(frozen=True)`. The flag is pure theater. Additionally, the 17 rules are CLASS attributes (lines 30-56) — instance-level `__setattr__` alone would NOT prevent `ImmutableRiskRules.RISK_PER_TRADE = 0.05` at class level. Fix: convert to `@dataclass(frozen=True)` OR add BOTH instance-level `__setattr__` AND metaclass `__setattr__` to prevent class-level mutation. Raise `AttributeError("ImmutableRiskRules cannot be modified at runtime")`. | `qualification/risk_sizer.py:30-59` | OPEN | 1h |
| 8 | R21-13/14 | Transition buffer orphaned + no VIX hysteresis (10-20 regime changes/day). `decrement_transition_buffer()` exists at regime_classifier.py:293-298 but is NEVER CALLED from any file. Buffer was silently reduced from 2→1 sessions (line 185 comment) without plan approval. VIX thresholds at 25/35/45 have ZERO deadband — no hysteresis structure exists. Fix: (1) Call `decrement_transition_buffer()` at end of each regime evaluation cycle, (2) Add per-threshold memory dict for VIX hysteresis: `{threshold: last_cross_direction}`, (3) Apply 5% symmetric deadband (e.g., VIX must cross 26.25 to enter HIGH_VOL but must drop below 23.75 to exit — 5% of 25), (4) Apply same deadband to SPY change threshold (-2.0). | `feeds/regime_classifier.py:185,293-298` | OPEN | 4h |
| 9 | R21-16 | Circuit breaker state not persisted — ALL state is in-memory only: `_halted_for_session`, `_halt_reason`, `_consecutive_losses`, `_cooldown_until`. Grep for "persist", "redis", "save_state" in circuit_breakers.py returns ZERO results. Docker restart = clean slate = halts bypassed. Fix: persist to Redis (system already uses Redis for Chandelier exit via `core/state_manager.py` with Lua scripts). Redis key: `nzt:circuit:{field}` for each stateful field. On boot, reconstruct state from Redis. Fail-CLOSED on corrupt/missing Redis state (assume halted until proven safe). | `qualification/circuit_breakers.py` | OPEN | 3h |
| 10 | R21-18 | Weekly/monthly halts — CONTRADICTORY + MISSING. `ImmutableRiskRules.MAX_WEEKLY_LOSS = 0.06` (6%) at risk_sizer.py:40, `SessionProtection.get_weekly_action()` at risk_sizer.py:432 halts at -6%, but Unified Threshold Table says -8%. Monthly logic completely absent. DECISION REQUIRED: align to -6% (code) or -8% (plan). Also: no weekly P&L aggregation mechanism exists, no implementation in circuit_breakers.py at all. Fix: (1) DECIDE weekly threshold: -6% or -8%, update ImmutableRiskRules:40 + SessionProtection:432 + Unified Threshold Table to match, (2) Add monthly -15% halt, (3) Weekly reset = Monday 00:00 UK, monthly reset = 1st of month, (4) Weekly query: `SELECT SUM(net_pnl) FROM virtual_trades WHERE exit_time >= date('now', 'weekday 1', '-7 days')`, (5) Persist to Redis (per R21-16). | `qualification/risk_sizer.py:40,432`, `qualification/circuit_breakers.py` | OPEN | 4h |
| 11 | **T-01** | **Remove first-30-min blackout — blocks highest-alpha window** | `daily_target.py:324-333` | **OPEN** | **3h** |
| 12 | **T-02** | **Remove lunch dead zone blackout — blocks US pre-market repricing** | `daily_target.py:335-344` | **OPEN** | **2h** |
| 13 | **T-03** | **Event-driven scanning — 60s heartbeat + anomaly trigger for S15** | `main.py` scheduler | **OPEN** | **8h** |
| 14 | **T-04** | **Move GPD tail risk to nightly batch — 24s latency per scan cycle** | `daily_target.py:414-435` | **OPEN** | **4h** |
| 15 | **T-05** | **Reweight indicators: FAST tier (VWAP/MACD/RSI/ROC) 3/4 for gap moves** | `daily_target.py:127-202` | **OPEN** | **6h** |
| 16 | **T-06** | **Lower ADX to 15 (FAST) / 20 (SLOW) — current 25 rejects trend starts** | `daily_target.py:77-79` | **OPEN** | **1h** |
| 17 | **T-08** | **Remove `_daily_signal_fired` — old V1 single-fire limit still active** | `daily_target.py:348,497` | **OPEN** | **0.5h** |
| 18 | **T-10** | **FAST path qualification: 7 gates, <500ms (current: 18 gates, 4.5s)** | `main.py:1823-2850` | **OPEN** | **6h** |
| 19 | **RO-01** | **Toxic spread hard-cap 35 bps for first 10 min (09:00-09:10 UK)** | `daily_target.py`, `cost_model.py` | **OPEN** | **2h** |
| 20 | **RO-02** | **3x instant-stopout circuit breaker = halt session** | `circuit_breakers.py`, `virtual_trader.py` | **OPEN** | **2h** |
| 21 | **RO-03** | **Underlying inventory limit (max 1 derivative per underlying)** | `portfolio_risk.py`, `isa_universe.py` | **OPEN** | **2h** |
| 22 | **AR-03** | **Walk-forward validation with purge/embargo for ML. FIX TARGET: `core/ml_meta_model.py` (NOT learning_engine.py — plan incorrectly cites learning_engine.py). Current code at ml_meta_model.py:287-288 uses `StratifiedKFold(n_splits=5, shuffle=True, random_state=42)` — `shuffle=True` on time-series data is WORST-CASE temporal leakage. Replace with expanding-window walk-forward split with 5-day purge window and 5-day embargo window (per RK-03 CPCV spec).** | `core/ml_meta_model.py:287-288` | **OPEN** | **6h** |
| 23 | **AR-04** | **Regime-conditioned Go-Live gates (40% WR per regime)** | `sprint6_live_gate.py`, `go_nogo.py` | **OPEN** | **4h** |
| 24 | **CR-01** | **SyntheticBroker — Local matching engine simulating LSE ETP queue priority + adverse selection. Cannot validate Ghost-Maker without it. yfinance has NO order book.** | `testing/synthetic_broker.py` | **OPEN** | **12h** |
| 25 | **CR-02** | **AsyncioHeartbeat — GIL freeze detector. If event loop blocked >50ms during Pandas ops, trip circuit breaker. Brain/Muscle separation is VOID without this.** | `core/asyncio_heartbeat.py` | **OPEN** | **6h** |
| 26 | **CR-03** | **ReconciliationAuditor — Out-of-band 5-min broker API truth vs local SQLite/Redis state comparison. SIGKILL + Market-On-Close on ANY mismatch. Prevents "Dark State" blowup.** | `core/reconciliation_auditor.py` | **OPEN** | **8h** |
| 27 | **CR-04** | **MicrostructureCalibrator — Walk-forward IC optimization for Tachyon SG window + Hawkes decay rate. Current params are hand-fitted = curve-fit risk.** | `core/microstructure_calibrator.py` | **OPEN** | **10h** |
| 28 | **GA-01** | **~~Real-time WebSocket data feed~~ RESOLVED (v16.1): IBKR IB Gateway integrated as primary data source via `ibkr_source.py`. Real-time L1 quotes + OHLCV bars. L2 LOB data available via `ib.reqMktDepth()` when Phase Q2 microstructure modules are implemented.** | `data_hub/sources/ibkr_source.py`, `docker-compose.yml` | **DONE** | **done** |
| 29 | **GA-02** | **SQLite → PostgreSQL migration — SQLite file-locking will crash under Disruptor multi-process writes. Brain, Muscle, Auditor concurrent access = guaranteed `database is locked` exceptions. PostgreSQL WAL mode supports concurrent readers/writers natively.** | `core/state_manager.py`, `core/db.py` | **OPEN** | **12h** |
| 30 | **GA-03** | **ProcessPoolExecutor for Brain — asyncio cooperative multitasking shares the GIL. Must move ALL Pandas/SciPy/NumPy computation to a separate OS process via `multiprocessing.Process` or `ProcessPoolExecutor`. Brain and Muscle must run in separate OS processes, not just separate coroutines.** | `core/disruptor_engine.py` | **OPEN** | **8h** |
| 31 | **GA-04** | **Broker commission audit & Capital Critical Mass Gate — Fixed £5/trade fees on £2,500 sub-positions = 0.40% round-trip drag, destroying Ghost-Maker's 0.20% spread capture. Must enforce MAX_CONCURRENT_POSITIONS=1 until equity > £25k. Verify IBKR Tiered pricing (0.05%, £1.00 min).** | `qualification/risk_sizer.py`, `config/settings.yaml` | **OPEN** | **2h** |
| 32 | **GA-05** | **Spread-Expansion Circuit Breaker for MOC emergency exits — ReconciliationAuditor's MOC orders during liquidity vacuums can fill 10-25% away from fair value. Must forbid Market Orders when spread > 50bps. Use passive Limit Order pegged to Mid-Price + consider inverse ETP hedge.** | `core/reconciliation_auditor.py`, `execution/ghost_maker.py` | **OPEN** | **4h** |
| 33 | **SK-01** | **THE EQUITY DENOMINATOR PHANTOM — Circuit breaker _starting_equity frozen at init, never updated. After equity growth (10K->30K), routine 1.5% daily loss calculates as 3.0% = false L2/L3 triggers. Emergency flattens on normal drawdowns destroy profitable positions. Fix: (1) Change reset_daily() signature from `reset_daily(self) -> None` (circuit_breakers.py:298) to `reset_daily(self, current_equity: float) -> None`. (2) Update caller in main.py to pass current equity from broker/virtual portfolio. (3) Set `self._starting_equity = current_equity` inside reset_daily(). (4) ALSO fix _starting_equity in dynamic_sizer.py:188 and sheets_logger.py:67 (hardcoded 10000.0). _starting_equity should be SESSION-OPEN equity (anchored at daily reset), NOT live equity (moving target) — otherwise drawdown % shifts intraday.** | `qualification/circuit_breakers.py:298,387`, `qualification/dynamic_sizer.py:188`, `delivery/sheets_logger.py:67` | **OPEN** | **1.5h** |
| 34 | **SK-02** | **THE ZOMBIE HALT — Consecutive loss query in _update_state_from_db() has NO date filter. THREE queries affected: (1) main.py:1176-1178 (virtual_trades, ORDER BY exit_time DESC), (2) main.py:1182-1184 (trades fallback, ORDER BY time_entered DESC — note: inconsistent column name), (3) delivery/database.py:1008-1022 (get_consecutive_losses() — a THIRD independent consecutive loss counter). After reset_daily() (circuit_breakers.py:298) clears in-memory state, next scan reloads stale loss streak from ALL THREE unfiltered DB queries, creating PERMANENT deadlock. Fix: add `WHERE exit_time >= datetime('now', '-12 hours')` to ALL THREE queries. Use exit_time consistently (not time_entered). Note: datetime('now') in SQLite is UTC — system operates on Europe/London. Use `WHERE exit_time >= datetime('now', '-12 hours')` which is safe because LSE session is ~7.5h, well under 12h.** | `main.py:1176-1184`, `delivery/database.py:1008-1022`, `qualification/circuit_breakers.py:298` | **OPEN** | **1h** |
| 35 | **SK-03** | **THE CONFIDENCE CEILING — S15 _MIN_CONFIDENCE=75.0 (daily_target.py:71) but Constitution R13 says min=65. ImmutableRiskRules enforces 60 (risk_sizer.py:45). Three conflicting confidence floors. 10-point gap silently rejects ~40% of valid signals even after ALL timing defects fixed. DECISION REQUIRED: align _MIN_CONFIDENCE=65 to match Constitution R13 (recommended — the Harvey & Liu correction is overly conservative for a momentum system with FAST/SLOW tiered gates that already filter quality). Update daily_target.py:71, risk_sizer.py:45 (MIN_CONFIDENCE=65), and remove the academic citation at line 71-72 that justifies 75.** | `strategies/daily_target.py:71`, `qualification/risk_sizer.py:45` | **OPEN** | **0.5h** |
| 36 | **SK-04** | **THE DUAL THROTTLE PARADOX — SessionProtection has TWO halt tiers: +2.0% (risk_sizer.py:362, returns halt:True) AND +1.5% (risk_sizer.py:370, also returns halt:True). The +1.5% tier makes 2% target architecturally unreachable. ADDITIONALLY, _daily_signal_fired (daily_target.py:297 init, :348 check, :497 set) blocks after first signal AND _MAX_SIGNALS_PER_DAY=1 (daily_target.py:70) is a redundant throttle. COUPLED FIX: (1) Remove the +1.5% halt tier at risk_sizer.py:370-376 (keep +2.0% halt at :362 as the sole session cap), (2) Delete _daily_signal_fired dict and all 3 references (:297, :348, :497-502), (3) Change _MAX_SIGNALS_PER_DAY from 1 to 4 (match portfolio governor), (4) Deploy all 3 changes in SAME commit. MUST be fixed simultaneously with T-08.** | `qualification/risk_sizer.py:362,370`, `strategies/daily_target.py:70,297,348,497` | **OPEN** | **1h** |
| 37 | **RI-01** | **IMAGE_PARITY deploy gate — Docker image digest MUST match git HEAD SHA at boot. Prevents stale container deploys where code has been updated but Docker image hasn't been rebuilt. Invariant: `env.IMAGE_DIGEST == git.HEAD_SHA`. On mismatch: `sys.exit(1)` with log "BOOT_PARITY_MISMATCH". Must be the FIRST check before any trading logic. Without this, operator can deploy weeks-old code that lacks critical fixes.** | `main.py` (Global Init), `Dockerfile` | **OPEN** | **2h** |

### P1 (58 Items — Complete Before Live Trading, was 56 +2 from Gemini Adversarial Q5/Q7)

| # | ID | Description | Status |
|---|-----|-------------|--------|
| 1 | R21-02 | Validate rung reach probabilities (shadow markout during paper) | OPEN |
| 2 | R21-26 | LSE Time-of-Day windows (currently US-only) | OPEN |
| 3 | R21-07 | Signal queue: remove dead-end or add consumer | OPEN |
| 4 | R21-09 | Three profit ladders -> one (VT inline canonical) | OPEN |
| 5 | R21-10 | ETPProfitLadder SHORT P&L sign fix | OPEN |
| 6 | R21-11 | ML regime map fix (GPT-58) — `_REGIME_MAP` at ml_meta_model.py:48 has keys `{"bull": 0, "bear": 1, "neutral": 2, ...}` but `RegimeState` enum (models.py:42) has values `TRENDING_UP_STRONG, TRENDING_UP_MOD, etc.`. `_encode_regime()` does `.get(str(regime).lower().strip(), -1)` — "trending_up_strong" never matches "bull". EVERY regime encodes as -1. Fix: replace keys with `{"trending_up_strong": 0, "trending_up_mod": 1, "trending_down_strong": 2, "trending_down_mod": 3, "range_bound": 4, "high_volatility": 5, "risk_off": 6, "shock": 7}`. | OPEN |
| 7 | R21-15 | SHOCK_RECOVERY counts signals not sessions | OPEN |
| 8 | R21-17 | Single Risk Arbiter for 12 flatten paths | OPEN |
| 9 | R21-22 | Replace pairwise correlation brake with max-per-cluster | OPEN |
| 10 | R21-23 | Portfolio heat cap 3.0% -> 3.5% (add headroom) | OPEN |
| 11 | R21-24 | Stale data tick-change counter | OPEN |
| 12 | R21-25 | Broker-side bracket orders (survive total system failure) | OPEN |
| 13 | R21-27 | overnight_kill=True for ALL ETPs (paper/limited live) | OPEN |
| 14 | R21-30 | ML feature leakage fix — "confidence" at ml_meta_model.py:74 is index 4 in `feature_cols`. `blend_confidence()` creates circular feedback: confidence → model input → ml_prob → blended output → next cycle's confidence input. Fix: remove "confidence" from feature_cols list. Add replacement features: `raw_indicator_count`, `spread_bps`, `time_since_regime_change_hours`. These must also be added to `_extract_row()` and the indicator pipeline. | OPEN |
| 15 | R21-32 | ML bypass enforcement during paper phase | OPEN |
| 16 | R21-34 | Max positions = 4 (respect R4 40% cap) | OPEN |
| 17 | R21-40 | Exit loop decoupling (10s exit cadence) | OPEN |
| 18 | T-07 | Lower RVOL thresholds: 0.30 FAST / 0.65 SLOW (was 0.85) | OPEN |
| 19 | T-09 | Pre-market intelligence scan (07:30 UK overnight futures) | OPEN |
| 20 | T-11 | Predictive entry timing (limit pullback + ML delay model) | OPEN |
| 21 | **CQ-01** | Volatility-scaled breathing room for stops (1-min ATR noise floor) | OPEN |
| 22 | **CQ-02** | Mid-price illusion filter (bid/ask trigger for FAST tier) | OPEN |
| 23 | **CQ-04** | Reversal recovery cooldown (15-min per-ticker after instant stopout) | OPEN |
| 24 | **CQ-05** | Cross-asset premium divergence filter | OPEN |
| 25 | **SA-02** | JIT-compile numerical qualification gates (numba) | OPEN |
| 26 | **SA-04** | LOBCache module for sub-microsecond heartbeat reads | OPEN |
| 27 | **RO-05** | Maker-pegged synthetic limit orders | OPEN |
| 28 | **CR-05** | Chaos Drill: "Pandas Fat Finger" — inject 200ms GIL block during live stop monitoring, verify heartbeat trips breaker | OPEN |
| 29 | **CR-06** | Chaos Drill: "Toxic Tsunami" — flood SyntheticBroker with adverse fills, verify Ghost-Maker abort after 3 consecutive toxic fills | OPEN |
| 30 | **CR-07** | Chaos Drill: "Phantom Fill" — SyntheticBroker reports fill but local state shows no position, verify ReconciliationAuditor catches mismatch within 5 min | OPEN |
| 31 | **CR-08** | Chaos Drill: "Redis Lobotomy" — kill Redis mid-trade, verify Disruptor Engine fails closed (no orphan positions, no phantom fills) | OPEN |
| 32 | **CR-09** | Chaos Drill: "Adverse Selection Sniper" — 80% of SyntheticBroker fills are immediately adverse, verify Ghost-Maker Toxicity Score > 70 triggers spread-cross abort | OPEN |
| 33 | **GA-06** | Token Bucket rate limiter — Mirror broker API limits locally (50 req/s, regen 10/s). Dynamic peg throttle: >80% consumed = Ghost-Maker timeout 800ms -> 3000ms. Reserve 20% for emergency flatten. | OPEN |
| 34 | **GA-07** | Single-Writer Actor Model — Only ONE coroutine (Execution Dispatcher) talks to broker API. Priority queue: P0=EMERGENCY_FLATTEN, P1=TOXICITY_CANCEL, P2=HAWKES_EXIT, P3=TACHYON_ENTRY. Prevents race conditions. | OPEN |
| 35 | **GA-08** | Synthetic Fair Value (SFV) Arbitrage engine — Compute real-time fair value: NQ=F * 3x * (GBP/USD) - swap accrual. Fire IOC when SFV diverges from LSE Ask by >2 ticks. Requires GA-01 WebSocket feed. | OPEN |
| 36 | **GA-09** | Micro-Price (Volume-Weighted Mid-Price) calculation — Replace naive mid-price with OBI-weighted: if 10k on Bid, 100 on Ask, true price is near Ask. Requires L2 data from GA-01. | OPEN |
| 37 | **GA-10** | TCP_NODELAY + TCP_QUICKACK on all broker sockets — Nagle's algorithm adds 10-40ms latency. Must set socket options explicitly on IBKR TWS API connections. | OPEN |
| 38 | **GA-11** | Spoof Detection Radar — Track order cancellation rates. If order >5x avg book size appears and disappears within 500ms unfilled, tag LOB as "Spoofed" and halt execution for 3 seconds. | OPEN |
| 39 | **GA-12** | Volatility Regime Kill-Switch for variance drag — If 5d ATR < 20d ATR (sideways market), ban 3x/5x ETP trading. Force 1x underlying or cash. L2*sigma2/2 decay is fatal in range-bound regimes. | OPEN |
| 40 | **GA-13** | Triangular Arbitrage Scrubber for GBP/USD — If cable moves >0.25% in 60s, disable SFV arbitrage entirely. Currency hedging noise, not genuine edge. | OPEN |
| 41 | **GA-14** | Nightly Combine genetic optimization — AWS Lambda at 22:00 UK: download tick data, run walk-forward genetic optimization on Tachyon/Hawkes params, push to Redis. Brain loads pre-optimized weights at 08:00. | OPEN |
| 42 | **GA-15** | Bare Metal / Dedicated Host migration — Move from t3.small shared tenancy to c7g.medium dedicated in eu-west-2 (London). Eliminates CPU steal-time, hypervisor jitter, noisy-neighbor latency spikes. | OPEN |
| 43 | **AB-01** | Daily loss limit contradiction — ImmutableRiskRules MAX_DAILY_LOSS=0.03 (3%) vs Circuit Breaker L3=0.04 (4%) vs L1=0.015. Two overlapping systems with different thresholds = undefined behavior during drawdowns. Fix: remove MAX_DAILY_LOSS from ImmutableRiskRules, let L1/L2/L3 cascade be sole daily-loss governor. | OPEN |
| 44 | **AB-02** | settings.yaml timezone=US/Eastern (line 9) but system trades LSE UK hours. Most code uses explicit Europe/London but any module reading primary timezone setting is off by 5 hours. Fix: change line 9 to timezone: Europe/London. | OPEN |
| 45 | **AB-03** | ib_insync event loop blocking — Ghost-Maker requires <10ms order modification but ib_insync's synchronous ib.sleep(0.5) calls block the asyncio event loop for 500ms+ per call. Must use ib_insync async mode with asyncio.sleep() instead. Fix: (1) replace all ib.sleep() with await asyncio.sleep(), (2) **DUAL EVENT LOOP SEPARATION (Gemini Q4)**: `ibkr_source.py` (client_id=10, data) and `ibkr_gateway.py` (client_id=2, execution) currently share the same Python process and therefore the same asyncio event loop. If data fetch blocks (e.g., slow `reqHistoricalData` response), execution orders are delayed. Fix: instantiate SEPARATE asyncio event loops for data vs execution — either via `util.patchAsyncio()` + `asyncio.run_coroutine_threadsafe()` cross-loop dispatch, or run `ibkr_gateway.py` in a dedicated `threading.Thread` with its own `asyncio.new_event_loop()`. All IBKR API calls must be awaited (never fire-and-forget). Enforcement: add `assert asyncio.get_running_loop() is self._expected_loop` at top of every IBKR callback to detect cross-loop contamination. | OPEN |
| 46 | **AB-04** | Circular dependency in execution module implementation — CR-01 validates Ghost-Maker, CR-04 calibrates Tachyon/Hawkes feeding Ghost-Maker, CR-04 requires data from running Ghost-Maker. Fix: 3-pass bootstrap with static defaults first, then SyntheticBroker data, then re-validate with calibrated params. | OPEN |
| 47 | **AB-05** | P1-13 (overnight_kill) should be elevated to P0 — Ruin math assumes 0.75% max loss per trade, but overnight gap on 3x ETP can cause 5-15% portfolio loss in single event, blowing through L3. Enforce overnight kill for ALL leveraged ETPs (3x AND 5x) during paper and limited live. | OPEN |
| 48 | **RI-02** | InvariantEnforcer module — Centralized runtime invariant checker that validates ALL 12 boolean predicates (Section 2H) at boot and every 60s during trading. On ANY invariant failure: log invariant name + actual vs expected values, set kill switch, alert P0. Must be impossible to disable without Constitution amendment. | OPEN |
| 49 | **RI-03** | Data feed staleness invariant — If >3 CORE tickers return identical price for 2 consecutive 60s cycles (stuck feed, not genuine flat market), enter DEGRADED mode. If >50% of universe returns stale data for >5 min, HALT all trading. Applies to BOTH IBKR and yfinance data sources. IBKR disconnect triggers automatic yfinance fallback (DataHub:78-82); if yfinance also stale, HALT. | OPEN |
| 50 | **RK-01** | **100-Trade Validation Gate — After T-01 through T-08 timing fixes, run EXACTLY 100 paper trades before proceeding to ANY other P0 item. Measure: win rate, avg winner, avg loser, Sharpe. If WR < 40%: STOP. S15 signal logic needs fundamental rework (not more infrastructure). If WR >= 40%: proceed to SK-01 through SK-04. This gate prevents spending 200+ hours on infrastructure for a signal engine that doesn't work.** | OPEN |
| 51 | **RK-02** | **Complexity Reduction: Defer Sections 2C/2D/2E/2F to Phase Q2+ — Ghost-Maker, Tachyon Trigger, Lead-Lag Arbitrage, Disruptor Engine, SyntheticBroker, and all microstructure modules are PHASE Q2+ features. They require: real-time WebSocket data (GA-01), PostgreSQL (GA-02), ProcessPool GIL bypass (GA-03) — none of which exist. Phase Q1 should focus ONLY on: S15 timing fixes, Silent Killers, ISA gate, basic risk controls. Reclassify all Sections 2C-2F P0/P1 items as Q2+ deferred.** | OPEN |
| 52 | **RK-03** | **ML Hardening: CPCV + Depth Limit — Enhance AR-03 (walk-forward validation): mandate Purged Combinatorial Cross-Validation (CPCV, de Prado 2018) with 5-day purge/embargo window. Restrict LightGBM ensemble to max_depth=2 to force broad rules, not noise memorization. 413 historical trades is far below the ~2,000 minimum for reliable gradient boosting. Until N>2,000, ML meta-model must remain in BYPASS mode (already P1-15). CRITICAL (Gemini Q9): Current N=52 makes CPCV catastrophically overfit — do NOT attempt ANY ML validation on Phase Q1 data. ML BYPASS is a HARD INVARIANT until N>500.** | OPEN |
| 53 | **QA-01** | **v16.0 Invariant 13: RUST_FFI_HEARTBEAT — Rust FFI sidecar must respond to PING within 500μs AND order struct SHA-256 checksum must match between Python and Rust. Enforce every order submission + 200ms heartbeat. On failure: sys.exit(1). Prevents stale/corrupt FFI bridge, memory layout mismatch, sidecar death. Phase Q3+ only.** | OPEN |
| 54 | **QA-02** | **v16.0 Invariant 14: DQN_ACTION_BOUND — DQN execution agent output must be within LEGAL_ACTION_SET AND position delta <= MAX_SINGLE_ORDER_SIZE AND epsilon == 0.0 (no exploration in production). On failure: flatten all + sys.exit(1). Prevents neural network hallucination, oversized orders, random exploration trades on leveraged ETPs. Phase Q4 only.** | OPEN |
| 55 | **QA-03** | **v16.0 Invariant 15: FIX_DROP_COPY_RECONCILE — Internal position must EXACTLY match FIX drop-copy position (integer shares, zero tolerance) for ALL tickers AND drop-copy age < 2 seconds AND FIX sequence gap == 0. On failure: flatten divergent ticker + halt. Replaces 5-min polling with tick-level reconciliation. Phase Q3+ only.** | OPEN |
| 56 | **QA-04** | **v16.0 Invariant 16: FRACDIFF_STATIONARITY_GATE — All fractionally differentiated ML input features must pass ADF stationarity test (p < 0.05) AND preserve memory correlation > 0.50 with original series AND Neural Hawkes residuals must pass Ljung-Box test (p > 0.05). On failure: block ML inference, degrade to rule-based signals. Phase Q4 only.** | OPEN |
| 57 | **GQ-01** | **IB Gateway background reconnection loop (Gemini Q5) — IB Gateway daily restart (IBC auto-restart) and Sunday re-auth both drop the WebSocket connection to `ibkr_source.py`. Current behavior: `IBKRSource.IS_AVAILABLE` flips to False, DataHub falls back to yfinance. Missing: NO active reconnection attempt. `ibkr_source.py` must contain a background reconnection loop: when `IS_AVAILABLE==False`, attempt `ib.connectAsync(host, port, clientId=10)` every 5 seconds for up to 10 minutes. Log each attempt. If reconnect succeeds: flip `IS_AVAILABLE=True`, re-subscribe to market data. If 10 minutes elapse without reconnect: send Telegram alert, remain on yfinance fallback. On Monday mornings, if reconnect fails for >30 minutes (2FA not approved), trigger GQ-02 HALT guardrail.** | OPEN |
| 58 | **GQ-02** | **Monday pre-market Go-NoGo guardrail (Gemini Q7) — Code a connectivity gate at 07:50 UK every trading day: if `not ib.isConnected()`, fire Telegram alert "IBKR DISCONNECTED — 2FA REQUIRED". If still not connected by 08:00 UK, set `nzt:halt_reason=IBKR_DISCONNECTED` in Redis and HALT all trading (not just degrade to yfinance). Rationale: yfinance data is 15-60s delayed with proxy spreads — trading gap signals (T-01) on stale data means buying gap tops. Monday is highest risk because 2FA approval is required after IB Gateway's weekend re-auth. The system must NOT silently degrade to yfinance and trade as if it has real-time data.** | OPEN |

**RULE: No live trading until ALL P0 items show VERIFIED. No exceptions.**

**EXECUTION ORDER**: Fix T-01 through T-08 FIRST (timing defects). Then original P0 items. A system with perfect risk controls but 0% win rate from late entries will never make money.

---

# SECTION 0.1: UNIFIED THRESHOLD SOURCE-OF-TRUTH TABLE {#section-01}

**This table is the FINAL AUTHORITY for all risk parameters. If plan text, code, or settings.yaml disagree with this table, THIS TABLE WINS.**

| Parameter | Value | Code Location | Notes |
|-----------|-------|---------------|-------|
| Per-trade risk cap | **0.75%** | `risk_sizer.py:41` | SACRED. IMMUTABLE. |
| Daily loss L1 (reduce 50%) | **-1.5%** | `circuit_breakers.py:43` | Intraday |
| Daily loss L2 (exit-only) | **-2.5%** | `circuit_breakers.py:44` | Intraday |
| Daily loss L3 (flatten all) | **-4.0%** | `circuit_breakers.py:45` | Intraday |
| Weekly loss halt | **-8.0%** (PLAN) vs **-6.0%** (code: ImmutableRiskRules.MAX_WEEKLY_LOSS at risk_sizer.py:40 + SessionProtection at :432) | PARTIALLY IMPLEMENTED (wrong threshold) | **P0-10: DECIDE -6% or -8%, implement in circuit_breakers.py** |
| Monthly loss halt | **-15.0%** | UNIMPLEMENTED | **P0-10: Must implement** |
| Max concurrent positions | **4** (DECISION: settings.yaml currently has 3 — change to 4 or update plan to 3) | `settings.yaml:622` (currently 3) | 4 x 10% = 40% total deployment. **CODE SAYS 3, PLAN SAYS 4 — MUST RECONCILE.** |
| Portfolio heat cap | **3.5%** | NEEDS UPDATE | Was 3.0%, raised for headroom |
| VIX -> HIGH_VOLATILITY | **>25** | `regime_classifier.py:128` | 5% deadband |
| VIX -> RISK_OFF | **>35** | `regime_classifier.py:135` | Kelly multiplier = 0.00 |
| VIX -> SHOCK | **>45** AND delta>10 | `regime_classifier.py:128` | Emergency flatten |
| VIX default (fail-closed) | **99.0** | `market_structure.py:489-496` (`_default_vix()` currently returns `{"vix": 0.0, "risk_level": "NORMAL"}`) | **P0-6: Change to `{"vix": 99.0, "risk_level": "EXTREME"}`** |
| SessionProtection halt | **+2.0%** | `settings.yaml:604` | Was +1.5% (kills 2% target) |
| Kelly fraction (55% WR) | **0.280** | Derived | f* = (0.55x1.667-0.45)/1.667 |
| Regime multiplier range | **0.00-0.60** | `dynamic_sizer.py` | RISK_OFF/SHOCK = 0.00 |
| VIX hysteresis deadband | **5%** | UNIMPLEMENTED | **P0-8** |
| ML bypass threshold | **N < 500** | - | Pure bypass during paper |
| Overnight kill (paper) | **ALL ETPs** | `settings.yaml` | **P1-13: Only 5x enforced** |
| Max per correlation cluster | **2** | `portfolio_risk.py` | **P1-9: Replace pairwise brake** |
| Min composite score | **65** | Constitution R13 | No trade below this. |
| **ADX threshold (FAST tier)** | **15** | `daily_target.py` | **Was 25. Catches trend starts.** |
| **ADX threshold (SLOW tier)** | **20** | `daily_target.py` | **Continuation trades.** |
| **MIN_RVOL (FAST tier)** | **0.30** | `daily_target.py` | **Was 0.85. Gap moves start on low vol.** |
| **MIN_RVOL (SLOW tier)** | **0.65** | `daily_target.py` | **Institutional participation building.** |
| **RVOL late-day trough** | **0.80** | `daily_target.py` | **Was 1.5. Unreasonable for low-vol window.** |
| **Opening observe window** | **5 min** | `daily_target.py` | **Was 30 min blackout. Now 5-min observe then gap scan.** |
| **Lunch confidence penalty** | **-10** | `daily_target.py` | **Was 90-min hard veto. Now soft penalty.** |
| **FAST indicator gate** | **3/4** | `daily_target.py` | **VWAP + MACD + RSI + ROC. For gap/momentum moves.** |
| **Signal-to-order target** | **<500ms** | `main.py` | **FAST path. Was 4.5s through 18-gate gauntlet.** |
| **GIL freeze trip threshold** | **50ms** | `core/asyncio_heartbeat.py` | **CR-02: Event loop lag > 50ms = Brain circuit breaker** |
| **Reconciliation audit interval** | **300s (5 min)** | `core/reconciliation_auditor.py` | **CR-03: Broker API truth check every 5 min** |
| **Reconciliation mismatch action** | **SIGKILL + MOC** | `core/reconciliation_auditor.py` | **CR-03: Hard fail-closed on ANY state mismatch** |
| **SG window walk-forward IC threshold** | **>0.03** | `core/microstructure_calibrator.py` | **CR-04: Min Information Coefficient for Tachyon SG params** |
| **Hawkes decay walk-forward IC threshold** | **>0.03** | `core/microstructure_calibrator.py` | **CR-04: Min IC for Hawkes alpha/beta calibration** |
| **SyntheticBroker adverse selection rate** | **30-50%** | `testing/synthetic_broker.py` | **CR-01: Default fill adversity for stress testing** |
| **Max queue position simulation** | **FIFO + pro-rata** | `testing/synthetic_broker.py` | **CR-01: Realistic LSE queue priority model** |
| **API rate limit (Token Bucket)** | **50 req/s, regen 10/s** | `execution/rate_limiter.py` | **GA-06: Mirror broker limits locally** |
| **API throttle trigger** | **80% consumed** | `execution/rate_limiter.py` | **GA-06: Ghost-Maker timeout 800ms -> 3000ms** |
| **SFV divergence threshold** | **>2 ticks** | `core/sfv_engine.py` | **GA-08: Fire IOC when MM lags repricing** |
| **Spread-Expansion hard cap (MOC)** | **50 bps** | `core/reconciliation_auditor.py` | **GA-05: Forbid market orders above this** |
| **GBP/USD flash scrub threshold** | **0.25% in 60s** | `core/sfv_engine.py` | **GA-13: Disable SFV arbitrage on cable spike** |
| **Variance drag kill-switch** | **5d ATR < 20d ATR** | `qualification/risk_sizer.py` | **GA-12: Ban 3x/5x ETPs in sideways markets** |
| **Spoof detection threshold** | **5x avg book + cancel <500ms** | `core/spoof_detector.py` | **GA-11: Halt execution 3s on spoof detect** |
| **Capital Critical Mass threshold** | **GBP 25,000** | `qualification/risk_sizer.py` | **GA-04: MAX_CONCURRENT=1 until this equity** |
| **Broker min commission target** | **<GBP 1.00 / 0.05%** | broker config | **GA-04: IBKR Tiered pricing required** |
| **Circuit breaker equity denominator** | **Current equity (NOT stale _starting_equity)** | `circuit_breakers.py:387` | **SK-01: Must refresh at session start** |
| **Consecutive loss DB query date filter** | **WHERE exit_time >= now()-12h** | `main.py:1176-1184` + `delivery/database.py:1008-1022` (THREE queries, not one) | **SK-02: Prevents cross-day deadlock** |
| **S15 confidence floor** | **65** (match Constitution R13) | `daily_target.py:71` (_MIN_CONFIDENCE = 75.0) | **SK-03: Currently 75, rejects 40% of valid signals** |
| **ImmutableRiskRules MIN_CONFIDENCE** | **65** (match Constitution R13) | `risk_sizer.py:45` (MIN_CONFIDENCE = 60) | **SK-03: Currently 60, misaligned** |
| **SessionProtection + _daily_signal_fired** | **Fix as coupled unit** | `risk_sizer.py:362` (+2.0% halt), `risk_sizer.py:370` (+1.5% halt — REMOVE), `daily_target.py:70` (_MAX_SIGNALS=1), `daily_target.py:297,348,497` (_daily_signal_fired) | **SK-04: Remove +1.5% halt, delete _daily_signal_fired, change _MAX_SIGNALS to 4 — ALL in same deploy** |
| **Primary timezone setting** | **Europe/London** | `config/settings.yaml:9` | **AB-02: Currently US/Eastern (WRONG)** |
| **ib_insync event loop mode** | **Async (NO ib.sleep()) + DUAL LOOP (data vs execution)** | `execution/ibkr_gateway.py`, `data_hub/sources/ibkr_source.py` | **AB-03: synchronous mode blocks stop monitoring. Dual event loops prevent data fetch blocking execution.** |
| **IMAGE_PARITY check** | **env.IMAGE_DIGEST == git.HEAD_SHA** | `main.py` (Global Init) | **RI-01: sys.exit(1) on mismatch** |
| **Invariant check interval** | **60s during trading hours** | `core/invariant_enforcer.py` | **RI-02: Kill switch on ANY invariant failure** |
| **Data feed staleness threshold** | **>3 tickers stuck 2 cycles OR >50% stale 5 min** | `core/invariant_enforcer.py` | **RI-03: DEGRADED/HALT on stale feed** |
| **100-Trade Validation Gate** | **100 paper trades after T-01-T-08, WR >= 40% AND median Entry Timing Score < 0.50 to proceed** | Go/No-Go gate | **RK-01 + M-06 (Gemini Q10): STOP if WR < 40% OR median ETS >= 0.50 — timing fixes failed** |
| **Phase Q1 scope boundary** | **T-01-T-08 + SK-01-SK-04 + R21-19 + basic P0s ONLY** | Implementation phases | **RK-02: 2C-2F items deferred to Phase Q2+** |
| **LightGBM max_depth** | **2** | `core/ml_meta_model.py` | **RK-03: Prevent noise memorization on N=413** |
| **Rust FFI heartbeat timeout** | **500 μs** | `core/rust_ffi_bridge.py` | **QA-01: sys.exit(1) if FFI sidecar response > 500μs** |
| **DQN exploration rate (production)** | **0.0 (ZERO)** | `strategies/dqn_execution_agent.py` | **QA-02: No random exploration on live leveraged ETPs** |
| **FIX drop-copy max age** | **2 seconds** | `core/fix_drop_copy_reconciler.py` | **QA-03: Halt if no drop-copy message in 2s** |
| **Fractional diff ADF p-value** | **< 0.05** | `core/quant_math/frac_diff.py` | **QA-04: Block non-stationary features from ML** |
| **IBKR reconnection interval** | **5 seconds (max 10 min)** | `data_hub/sources/ibkr_source.py` | **GQ-01: Background reconnect loop when IS_AVAILABLE==False** |
| **Monday Go-NoGo check time** | **07:50 UK alert, 08:00 UK HALT** | `main.py` (scheduler) | **GQ-02: HALT if IBKR not connected by 08:00 UK (no yfinance gap trading)** |
| **VIX intraday spike GPD invalidation** | **VIX delta > 10 points from session open** | `main.py` (regime check) | **T-04 amendment: Invalidate nightly GPD cache on black swan VIX spike** |

---

# SECTION 0.2: REALISTIC SCENARIO TABLE {#section-02}

The system scans 60+ LSE leveraged ETPs across a 3-tier universe (CORE/PEER/FULL_SCAN), can fire multiple signals per day, and can open/close/reopen the same ticker within a session. Trading frequency is NOT the binding constraint — portfolio heat, correlation limits, and signal quality are.

| Scenario | Trades/Day | Trades/Year | Net Per Trade | Year 1 Equity | Annual Return |
|----------|-----------|-------------|---------------|---------------|---------------|
| **Quiet Market** | 0-1 | ~150 | +0.4% | ~£18,200 | +82% |
| **Base Case** | 1-2 | ~300 | +0.4% | ~£33,200 | +232% |
| **Active Market** | 2-3 | ~500 | +0.3% | ~£44,800 | +348% |
| **High Conviction** | 2-4 | ~400 | +0.5% | ~£73,900 | +639% |
| **MVP TARGET** | **1-2** | **~300** | **+0.3-0.5%** | **~£24,500-£44,800** | **+145-348%** |
| Theoretical Ceiling | 1/day perfect | 252 | +2.0% | ~£1,470,000 | +14,600% |

**Key principles**:
- **The MVP TARGET row is the REAL goal.** 0.3-0.5% average daily net return is still extraordinary (145-348% annualised) and would outperform 99.9% of systematic funds. This is the operationally realistic target for Phase Q1-Q2.
- The "Theoretical Ceiling" row ((1.02)^252 = ~147x) has never been achieved by any systematic fund in recorded history. It is the mathematical upper bound, not a target. Operator's reference frame should be MVP to Active (145%-348%).
- On days with no qualifying setups, the system stays flat. **No forced trades. Cash is a position.**
- The binding constraints are: (a) signal quality threshold (min 65), (b) portfolio heat cap (3.5%), (c) max 4 concurrent positions, (d) correlation brake.
- More trades does NOT equal more profit — quality is everything. 2 high-quality trades at 60% WR beats 5 mediocre trades at 45% WR.

**Kelly math (why the profit ladder makes this work)**:
- With the VT inline 6-rung ladder, blended average winner = ~+5.0%, average loser = -3.0%
- Payoff ratio b = 5.0/3.0 = 1.667
- At WR=55%: Kelly f* = 0.280 (strongly positive)
- At WR=50%: Kelly f* = 0.200 (still positive)
- The ladder's tail capture converts modest directional accuracy into asymmetric payoff

**Ruin math**: 6 consecutive losers in a single session = L3 halt (-4.0% daily, additive). 92 consecutive losers = 50% DD (compound). 306 consecutive losers = 90% DD (compound). Max single-cycle loss with 4 positions = 3.0% (breaches L2, not L3 — the 100 bps buffer to L3 exists to absorb gap risk on 3x ETPs).

---
