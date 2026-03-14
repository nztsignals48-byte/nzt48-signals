# NZT-48/AEGIS FULL-SPECTRUM INSTITUTIONAL + ACADEMIC SYSTEM AUDIT

**Date**: 2026-03-08
**System**: NZT-48 Automated Leveraged ETP Trading System
**Plan**: AEGIS Master Plan v16.2 (2,414 lines, 98 stop-ship items)
**Codebase**: ~9,300 lines (main.py) + ~25 modules
**Deployment**: EC2 c7i-flex.large, Docker Compose (3 containers)
**Mode**: Paper trading, £10,000 starting equity, UK ISA
**Track Record**: 0 wins / 52 trades (0% win rate)

---

## SECTION A: EXECUTIVE VERDICT

### A-1. Verdict

**NOT READY FOR LIVE CAPITAL**

### A-2. Rating

**RED — Critical structural defects across execution, risk, and data subsystems.**

The system is an ambitious, well-researched, and extensively documented trading framework. It contains over 30 discrete subsystems covering nearly every institutional risk concern found in the academic literature. However, the engineering reality diverges from the plan in multiple critical ways. The system has never produced a single winning trade in 52 attempts. The root causes are architectural, not parametric.

### A-3. Critical Findings (Tier 1 — Each Is Independently Disqualifying)

**FINDING C-01: The execution path that matters can silently fail to start.**
The TickLoop (`command_center/tick_loop.py`) — the ONLY component that actually opens positions via `virtual_trader.open_position()` (`tick_loop.py:787`) — is wrapped in a try/except at `main.py:9222-9225` that catches all exceptions and logs a warning. If it fails, `self._tick_loop = None`, and the system continues to scan, generate signals, and push them to the dashboard, but zero positions are ever opened. The signal queue consumer at `main.py:6829-6878` explicitly does NOT open positions — it only logs and pushes to API. The system can run indefinitely, appearing healthy, while doing nothing.

**FINDING C-02: Three different confidence floors create a contradictory gate.**
- `ImmutableRiskRules.MIN_CONFIDENCE = 65` (`risk_sizer.py:57`) — constitutional, immutable
- `ImmutableRiskRules.CONSECUTIVE_LOSSES_3_CONF = 75` (`risk_sizer.py:59`) — after 3 losses
- `gate_diagnostics.py:62` uses `_MIN_CONFIDENCE = 55.0` as a fallback
- `daily_target.py:75` uses `_MIN_CONFIDENCE = 65.0`
- `ThresholdRegistry.confidence_floor = 65` (`threshold_registry.py:99`)

The ThresholdRegistry (AEGIS E-01) was designed as the single source of truth ("Blood Oath #1"), but `daily_target.py`, `risk_sizer.py`, `gate_diagnostics.py`, and `universal_scanner.py` each hardcode their own floor. `gate_diagnostics.py` uses 55, which is 10 points below the constitutional floor. Signals can be diagnosed as "passing" in gate diagnostics while failing the constitutional check.

**FINDING C-03: Three different weekly loss halt thresholds exist simultaneously.**
- `ImmutableRiskRules.MAX_WEEKLY_LOSS = 0.06` (`risk_sizer.py:52`) — 6%, constitutional
- `SessionProtection.get_weekly_action()` triggers at `-0.06` (`risk_sizer.py:462`) — 6%
- `circuit_breakers._WEEKLY_DD_HALT = 0.08` (`circuit_breakers.py:120`) — 8%
- AEGIS plan specifies 8% (`aegis/07_RISK.md:16`)

The circuit_breakers module is documented as "SOLE CANONICAL AUTHORITY" for all P&L-based halts (`circuit_breakers.py:5-8`), but ImmutableRiskRules still enforces its own 6% weekly check at `risk_sizer.py:129-132`. The system will halt at 6% (the more restrictive value), but the plan expects 8%.

**FINDING C-04: S16 Universal Scanner emits SHORT signals with no ISA long-only guard.**
S16 (`universal_scanner.py`) explicitly supports SHORT direction signals in its gap scanner (`universal_scanner.py:473`), VWAP bounce scanner (line 527), momentum breakout (line 579), RSI reversal (line 635), and sector rotation (line 692). S15 has a proper ISA guard at `daily_target.py:1110-1116` that converts or blocks SHORTs. S16 has no equivalent. SHORT signals from S16 flow through the S16 medium gauntlet and into `virtual_trader.open_position()` without being blocked. `open_position()` (`virtual_trader.py:924`) has no ISA direction filter. This would attempt to short-sell in a UK ISA — an illegal operation.

**FINDING C-05: ML meta-model is explicitly disabled but the code remains wired.**
`_ML_ENABLED = False` at `ml_meta_model.py:56` (AEGIS 0-05). When False, `meta_label()` returns pass-through, `predict_proba()` returns 0.5, `train()` is no-op. However, the system still constructs the MLMetaModel object, loads its dependencies, and calls it on every signal. More critically, if someone sets `_ML_ENABLED = True`, the model trains on fabricated data (43.7% fabricated per the comment at `ml_meta_model.py:52`) and uses broken regime encoding that always returns -1.

**FINDING C-06: 98 stop-ship items (40 P0 + 58 P1) in the AEGIS plan, zero implemented.**
The plan itself (AEGIS_COMPLETE.md) documents 98 items that must be completed before live trading. The code has zero of these fully implemented and verified.

### A-4. Significant Findings (Tier 2 — Material Risk)

**FINDING S-01: SessionProtection halts at +2.0% daily P&L (`risk_sizer.py:397`), which equals the daily target.** The system's maximum theoretical daily gain is exactly its target — zero upside beyond 2.0%. After costs, the maximum achievable daily return is ~1.8%.

**FINDING S-02: Redis uses global `noeviction` policy (`docker-compose.yml:98`).** DB 0 (critical state) requires noeviction — correct. DB 1 (telemetry) was designed for `allkeys-lru` (`redis_config.py:11-13`) but Redis 7 applies maxmemory-policy globally. When the 400MB limit is hit, ALL writes fail — including position state and circuit breaker persistence.

**FINDING S-03: Fear & Greed Index is correctly CNN equities** (`cross_asset_macro.py:310` fetches from `production.dataviz.cnn.io`), NOT crypto as prior analysis suggested. However, CNN's Fear & Greed API is undocumented and unauthenticated.

**FINDING S-04: Kill switch has TWO implementations with different persistence.** (1) `KillSwitch` class (`telegram_bot.py:1820-1863`) — file-based, (2) `StateManager.set_kill()` (`state_manager.py:317-331`) — Redis hash with no TTL. `KillSwitch.deactivate()` only clears the file, not Redis.

### A-5. Correction to Prior Analysis

| Prior Finding # | Claim | Actual Code Evidence | Correction |
|---|---|---|---|
| #12 | ORANGE CB size_multiplier = 0.0 | `circuit_breakers.py:910` `size_multiplier = 0.50` | **WRONG. ORANGE is 0.50, not 0.0.** |
| #13 | Fear & Greed index used is for CRYPTO | `cross_asset_macro.py:310` fetches from CNN | **WRONG. It is CNN equities F&G.** |
| #16 | Weekly halt at -6% in code, -8% in plan | `risk_sizer.py:52` = 6%, `circuit_breakers.py:120` = 8% | **PARTIALLY RIGHT. Both values exist.** |

### A-6. Bottom Line

1. The system has never produced a profitable trade (0/52).
2. The execution path can silently fail to start.
3. Three independent confidence floors, three independent weekly halt thresholds, and two independent kill switches create contradictory authority.
4. S16 can emit illegal SHORT signals in a UK ISA.
5. 98 stop-ship items remain unaddressed.

No live capital should be deployed until the 0/52 track record is explained, the authority conflicts are resolved to single canonical sources, and the stop-ship list is materially reduced.

---

## SECTION B: FULL SYSTEM WIRING CHECK (25 Subsystems)

### B-01. Signal Generation — S15 Daily Target
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | `main.py:2399` filters `s.strategy == "S15"`, calls `_execute_s15_priority_path()` at `main.py:4221`. S15 scans tickers, applies data freshness gate (`daily_target.py:625`, 120s max), multi-confirmation gate, weighted indicator consensus (`daily_target.py:1298`). ISA SHORT guard at `daily_target.py:1110-1116`. |

### B-02. Signal Generation — S16 Universal Scanner
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL** |
| **Evidence** | `main.py:1535` loads `UniversalScannerStrategy`. S16 signals routed via `_check_s16_gauntlet()` at `main.py:2424`. However, SHORT signals emitted by gap scanner (`universal_scanner.py:473`), momentum breakout (line 579), RSI reversal (line 635), and sector rotation (line 692) have NO ISA long-only guard. |
| **Fix** | Add ISA long-only guard mirroring S15 pattern at `daily_target.py:1110-1116`. |

### B-03. Signal Queue Consumer
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL** |
| **Evidence** | Started as asyncio task at `main.py:9245`. Drains queue, logs signals, persists via signal_logger (`main.py:6857-6861`), pushes to API (`main.py:6866-6869`). Does NOT open positions. |

### B-04. TickLoop (Command Center)
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL — CRITICAL** |
| **Evidence** | `await self._tick_loop.start()` at `main.py:9222`. Position opening confirmed at `tick_loop.py:787`. **CRITICAL**: entire block wrapped in try/except at `main.py:9205-9226` that catches all exceptions and sets `self._tick_loop = None`. Silent failure. |
| **Fix** | TickLoop start failure should be FATAL. Set kill switch on failure. |

### B-05. Virtual Trader
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | `open_position()` at `virtual_trader.py:924` with 10+ gates. Chandelier registration at line 1252. Position persistence at line 1265. No ISA direction guard in `open_position()`. |

### B-06. Chandelier Exit (Profit Ladder)
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Registered on position open at `virtual_trader.py:1252`. Profit ladder delegates at `virtual_trader.py:1679`. Redis persistence confirmed. Leverage-adjusted ATR multipliers at `chandelier_exit.py:46-50`. |

### B-07. Circuit Breakers
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Called at `main.py:2099`. RED triggers emergency flatten at `main.py:2113-2122`. State persisted to Redis (`circuit_breakers.py:393-414`). Hydrated from Redis on startup (`circuit_breakers.py:417-510`). |
| **Fix** | Resolve 6% vs 8% weekly threshold conflict with `risk_sizer.py:52`. |

### B-08. ImmutableRiskRules (Constitutional)
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL** |
| **Evidence** | `__setattr__` guard enforces immutability post-init (`risk_sizer.py:37`). However, `MAX_WEEKLY_LOSS = 0.06` (line 52) still enforced at line 129-132, conflicting with circuit_breakers' 0.08. Class-level mutation is possible (no metaclass guard). |

### B-09. Emotional Firewall
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Revenge trading block at `risk_sizer.py:260-268`. Cooldown enforced at line 268. Hard blocks checked at line 380-382. |

### B-10. SessionProtection
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL** |
| **Evidence** | Daily halt at +2.0% (`risk_sizer.py:397`). Weekly halt at -6% (`risk_sizer.py:462`). Daily halt = target = zero headroom. Weekly halt fires before circuit_breakers' -8%. |

### B-11. Dynamic Sizer (12-Factor Kelly)
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Constitutional cap at `dynamic_sizer.py:64` `_IMMUTABLE_MAX_RISK_PCT = 0.0075` (0.75%). 12 factors documented. Regime multipliers (RISK_OFF=0.0, SHOCK=0.0). Total deployment cap at line 67 = 40%. |

### B-12. ThresholdRegistry (E-01)
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL** |
| **Evidence** | `@dataclass(frozen=True)` at `threshold_registry.py:76`. `confidence_floor = 65` at line 99. However, `daily_target.py:75`, `gate_diagnostics.py:62`, `universal_scanner.py:17` all hardcode their own floors, bypassing the registry. |

### B-13. Regime Classifier
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Called at `main.py:2092`. VIX thresholds: HIGH_VOL=25, RISK_OFF=35, SHOCK=45 (`regime_classifier.py:59-61`). 2-session transition buffer (line 123). |

### B-14. Cross-Asset Macro
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | VIX cache 5-min TTL. CNN Fear & Greed at line 310. HMM weekly refit. Risk-off detection at line 499-506. |

### B-15. Data Feed Validator
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:81`. S15 has independent data freshness gate at `daily_target.py:625` (120s max). |

### B-16. Kill Switch (Dual Implementation)
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | File-based at `telegram_bot.py:1839`. Redis at `state_manager.py:311` (no TTL). Both checked at scan entry: `main.py:1720` (file) and `main.py:1731-1736` (Redis). |
| **Fix** | `KillSwitch.deactivate()` should also clear Redis kill state. |

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
| **Wired?** | **PARTIAL (Dead Code)** |
| **Evidence** | S15 signals BYPASS this pipeline entirely via `_execute_s15_priority_path()` (`main.py:4229`). S16 signals also bypass via `_check_s16_gauntlet()`. Only non-S15/non-S16 signals use the full pipeline. Since S15 and S16 are the only active strategies, the full pipeline is effectively dead code. |

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
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:66`. Aggregates 4 subsystems (`learning_engine.py:442-448`). With 0/52 win rate, learning has no positive signal to learn from. |

### B-23. ML Meta-Model
| Field | Detail |
|-------|--------|
| **Wired?** | **NO (Intentionally Disabled)** |
| **Evidence** | `_ML_ENABLED = False` at `ml_meta_model.py:56`. AEGIS 0-05 documents broken regime encoding, confidence feature leakage, 43.7% fabricated training data. |

### B-24. Smart Router
| Field | Detail |
|-------|--------|
| **Wired?** | **YES** |
| **Evidence** | Imported at `main.py:77`. Scores liquidity, caps position sizes, predicts slippage (`smart_routing.py:118-122`). |

### B-25. Redis State Persistence
| Field | Detail |
|-------|--------|
| **Wired?** | **PARTIAL** |
| **Evidence** | DB 0 for critical state, DB 1 for telemetry. `maxmemory 400mb --maxmemory-policy noeviction` (`docker-compose.yml:98`). noeviction is global — DB 1 telemetry has no separate LRU policy. When 400MB is full, ALL writes fail. |

### Section B Summary

| Status | Count | Subsystems |
|--------|-------|------------|
| **YES** | 16 | B-01, B-05, B-06, B-07, B-09, B-11, B-13, B-14, B-15, B-17, B-18, B-20, B-21, B-22, B-24 |
| **PARTIAL** | 7 | B-02, B-03, B-04, B-08, B-10, B-12, B-19, B-25 |
| **NO (Intentional)** | 1 | B-23 |
| **CRITICAL** | 2 | B-02 (illegal SHORT), B-04 (silent TickLoop failure) |

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

### C.2 Who Opens Positions? (Three Independent Openers)

| Opener | Trigger | Position Limit | File:Line |
|--------|---------|---------------|-----------|
| main.py S15 priority path | APScheduler scan (60s) | max 1 S15 position | `main.py:4633` |
| main.py general gauntlet | APScheduler scan (60s) | max_concurrent_positions (3 or 4?) | `main.py:3338` |
| TickLoop execution bridge | TickLoop Brain (30s) | MAX_CONCURRENT=2 | `tick_loop.py:787` |

**RACE CONDITION**: Position count check happens OUTSIDE the RLock. Between check and `open_position()` call, the other loop can sneak in a position.

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
| 14 | InvariantEnforcer | `invariant_enforcer.py:68` |

DisciplineEngine is called at THREE separate locations (`main.py:4269`, `main.py:4781`, `main.py:3352`). If a new path is added and forgets the discipline check, it bypasses the "absolute authority."

### C.4 Can Two Modules Disagree and Both Execute?

**VERDICT: NO — but failure mode is OVER-REJECTION, not over-execution.** The system uses AND-composition: every gatekeeper must pass. However, the TickLoop execution bridge does NOT check the main.py circuit breaker state directly. The VirtualTrader's internal CB check at `open_position()` line 950-957 saves this, but ONLY if the CB state has been pushed within 120 seconds.

### C.5 Can Stale Data Pass Through?

**VERDICT: YES — multiple paths allow stale data to reach execution.**

1. **TickLoop yfinance fallback** (`tick_loop.py:926-942`): Falls back to `yf.download()` which returns 15-minute-delayed data. Staleness check at line 937 only checks FETCH LATENCY, not DATA AGE.
2. **OHLCV Cache TTL of 15 seconds** (`tick_loop.py:96`): If yfinance returns stale data, it gets cached for 15s.
3. **Main scan loop** (`main.py:1881-1883`): On weekends or after-hours, last-known price passes through to indicator computation.
4. **Reconciliation loop** (`main.py:6900-6919`): If both feeds return stale values, VirtualTrader updates stops against stale prices.

### C.6 Can Delayed Signals Still Fire?

**VERDICT: YES — signal queue has no TTL enforcement.** `PrioritySignalQueue(maxsize=50)` at `main.py:1276` has no timestamp-based expiry. TickLoop's drift check at line 770-776 (max 0.5% drift) provides partial protection.

### C.7 Can Multiple Exit Conditions Be True Simultaneously?

**VERDICT: YES — LAST-WRITER-WINS in update_prices loop.** `VirtualTrader._update_prices_locked()` (`virtual_trader.py:1319-1600`) checks exits in priority order with `continue` statements. Items 5-6 (overnight kills) do NOT use `continue` and can accumulate. Chandelier Exit runs SEPARATELY in the reconciliation loop at `main.py:6926-6939`.

### C.8 Can Restarts Clear Kill-Switch State?

**VERDICT: PARTIALLY.** File-based `_process_killed` is in-memory — CLEARED ON RESTART. File-based kill at `data/KILL_SWITCH` persists if on volume. Redis `nzt:kill` hash (no TTL) SURVIVES RESTART. GAP: TickLoop checks only `self._state.halt_new_signals`, NOT Redis StateManager kill switch directly. Kill switch check in `run_scan()` runs on APScheduler, not at boot.

### C.9 Can the System Think It Is Safe When a Dependency Is Dead?

**VERDICT: YES — multiple silent failures.**

1. **TickLoop fails silently** (`main.py:9224-9226`): WARNING, not ERROR/CRITICAL.
2. **IBKR disconnection degrades silently** (`tick_loop.py:916-924`): Falls back to yfinance with `logger.debug`.
3. **Redis fallback masks failure** (`state_manager.py:128`): In-memory mode reports as "healthy" — restart loses all state.
4. **InvariantEnforcer init failure** (`main.py:1295-1296`): "Non-critical" — 12-invariant safety net silently absent.

---

## SECTION D: EXECUTION TIMING AUDIT

### D.1 Current Timing Architecture

Three-layer architecture:

| Layer | Cadence | Purpose | Data Source |
|-------|---------|---------|-------------|
| APScheduler | Fixed times (06:00, 08:00, 12:00, 14:30, 16:00, 19:00, 20:30 UK) + 10s reconciler | Full strategy pipeline (S15/S16) | yfinance (sequential) |
| TickLoop Brain | 30s active / 120s off-hours | Lightweight momentum scan + execution | yfinance/IBKR |
| TickLoop Sniper | 5s | Position monitoring (stops, targets) | IBKR primary, yfinance fallback |

**DIAGNOSIS**: Two independent signal generators (APScheduler + TickLoop Brain) feeding the SAME VirtualTrader, uncoordinated. Both can try to open positions on the same ticker in the same minute.

### D.2 Exact Timing Bottlenecks (Ranked)

| Rank | Bottleneck | Estimated Latency | File:Line |
|------|-----------|-------------------|-----------|
| 1 | **yfinance Serial Data Fetch** | 24-72s per scan (12 tickers x 2 calls x 1-3s) | `main.py:1878-1899` |
| 2 | **TickLoop SignalEngine.run()** | 6-15s per tick (12 tickers via yfinance) | `tick_loop.py:500-510` |
| 3 | **Sniper Loop Serial Price** | 2-6s for 3 tickers on yfinance fallback | `tick_loop.py:1006-1013` |
| 4 | **S15 Confidence Modifiers** | 2-5s per signal (9+ modifiers) | `main.py:4309-4631` |

### D.3 Is the System Structurally Late?

**VERDICT: YES — 30-84 seconds late on entries, 5-16 seconds late on exits.**

**Entry Latency (APScheduler path):**
| Component | Latency |
|-----------|---------|
| Data ingestion (12 tickers sequential) | 24-72s |
| Strategy evaluation | 2-5s |
| Confidence modifiers | 2-5s |
| Qualification pipeline | 1-2s |
| VirtualTrader.open_position | <100ms |
| **TOTAL** | **29-84s** |

**Entry Latency (TickLoop path):**
| Component | Latency |
|-----------|---------|
| SignalEngine.run (12 tickers) | 6-15s |
| Quality gates + execution | 1-2s |
| Fresh price + drift check | 1-3s |
| **TOTAL** | **8-20s** |

**Exit Latency:**
| Scenario | Latency |
|----------|---------|
| Best case (IBKR) | 5.1s (sniper interval + IBKR) |
| Worst case (yfinance) | 11-16s (sniper + yfinance) |

**ROOT CAUSE**: System is built on yfinance polling, not event-driven streaming.

### D.4 Recommended Architecture — NOW (Phase B)

1. **Parallelize Data Ingestion** (`main.py:1878-1899`): `ThreadPoolExecutor` for yfinance calls. 12 tickers in ~3-5s instead of 24-72s.
2. **IBKR-Primary Data Path** (`tick_loop.py:903-944`): Auto-reconnect, health monitoring, Telegram alert on disconnection.
3. **Eliminate Dual Signal Generation**: APScheduler generates candidates → PrioritySignalQueue. TickLoop Brain becomes execution-only.
4. **Pre-Warm Data Cache at 07:55** (`main.py:5503-5512`): Warm OHLCV cache before 08:00 open.

### D.5 Recommended Architecture — LATER (Phase C — Streaming)

1. **Event-Driven IBKR Streaming**: `reqMktData()` push to ring buffer. <100ms latency.
2. **Redis Streams for Signal Pipeline**: Replace in-memory queue with Redis Streams (`XADD`/`XREADGROUP`).
3. **Separate Signal Generator Process**: Decouple data-heavy signal generation from execution.
4. **Rust FFI for Critical Path**: Replace Python `update_prices()` with Rust. <1us per position check.

### D.6 Recommended Cadence Stack

**NOW:**
| Loop | Cadence | Purpose |
|------|---------|---------|
| Signal Generator | 60s | Full strategy pipeline (yfinance parallel) |
| TickLoop Brain | 30s active / 120s off | Lightweight scan + execution bridge |
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

### E-1. Incremental Learner (Passive-Aggressive Online Classifier)
- **Classification**: THEATRE
- **Evidence**: `learning/incremental_learner.py:25-113`. PA classifier with C=1.0. Partial_fit on every trade (line 88). 52 updates, all label=0 (LOSS). Weight vector points entirely toward predicting LOSS.
- **Feature leakage**: `confidence` is an input feature (line 69). If confidence influences the label (which it does — higher confidence trades are taken with larger size), this is circular.
- **Live Influence**: None. Output not wired to any gate.

### E-2. Drift Detector (Page-Hinkley)
- **Classification**: PARTIAL
- **Evidence**: `learning/drift_detector.py:27-73`. Lambda=50 (default). At N=52, cumulative sum cannot overcome lambda=50 with uniformly bad outcomes. Threshold too high to trigger.
- **Live Influence**: None.

### E-3. Bayesian Win Rate Estimator
- **Classification**: PARTIAL
- **Evidence**: `learning/bayesian_estimator.py`. Beta(2,2) prior, posterior at N=52 with k=0 wins → Beta(2,54). Posterior mean = 2/56 = 3.6%. MAP = 1/54 = 1.9%. 95% HDI = [0.4%, 11.2%].
- **Live Influence**: None (output not wired to Kelly or any gate, but SHOULD be).

### E-4. Active Learning Weighting
- **Classification**: THEATRE
- **Evidence**: `learning/active_weighter.py`. Uses `signal.confidence` (post-modifier) as uncertainty proxy. Should use model prediction uncertainty, not signal confidence.
- **Live Influence**: None.

### E-5. Ensemble Diversity
- **Classification**: THEATRE
- **Evidence**: `learning/ensemble_diversity.py`. Needs 500+ trades for meaningful diversity measurement. In-sample evaluation only. Output unused.
- **Live Influence**: None.

### E-6. Reward Shaping
- **Classification**: INVALID
- **Evidence**: R-multiples loaded from trade outcomes but never shaped or used for any policy update. Citation to Ng et al. (1999) unsupported by implementation.
- **Live Influence**: None.

### E-7. Setup Fingerprint Library
- **Classification**: THEATRE (plan-only)
- **Evidence**: Referenced in AEGIS plan but no implementation exists. Needs 500+ trades.
- **Live Influence**: None.

### E-8. Nightly Activation Cycle
- **Classification**: PARTIAL
- **Evidence**: APScheduler triggers nightly batch at 21:30. Runs tournament, attribution, edge decay. ML-dependent components are inert (disabled).
- **Live Influence**: Diagnostic only.

### E-9. Meta-Labelling (De Prado 2018)
- **Classification**: PARTIAL (correctly disabled)
- **Evidence**: `ml_meta_model.py:729-794`. `_ML_ENABLED = False` (line 56). Returns pass-through `{veto: False, p_success: 0.5}`. Regime-adaptive thresholds (0.60 trending, 0.70 choppy, 1.0 shock). Feature leakage fixed (J-01). Walk-forward fixed (J-03).
- **Prerequisites**: 200+ genuine trades, regime map fixed, feature leakage removed, walk-forward passes.
- **Live Influence**: None (correctly).

### E-10. Walk-Forward Validation
- **Classification**: PARTIAL
- **Evidence**: `ml_meta_model.py:491-566`. Expanding-window with 5-day purge/embargo. Correctly implements De Prado CPCV after J-03 fix. With N=52, produces at most 1 fold — insufficient.
- **Live Influence**: None.

### E-11. SHAP / Feature Stability
- **Classification**: THEATRE (correctly gated)
- **Evidence**: `ml_meta_model.py:872-1072`. TreeExplainer, rank drift threshold=5, minimum 4 features kept. Never executed because ML is disabled.
- **Live Influence**: None.

### Summary Matrix

| # | Component | Classification | Live Influence | Min Data | Current N |
|---|-----------|---------------|---------------|----------|-----------|
| 1 | Incremental Learner | THEATRE | None | 200+ trades, >30% WR | 52 @ 0% WR |
| 2 | Drift Detector | PARTIAL | None | ~100+ trades | 52 |
| 3 | Bayesian Win Rate | PARTIAL | None (should be) | 1+ | 52 |
| 4 | Active Learning | THEATRE | None | N/A (wrong input) | 52 |
| 5 | Ensemble Diversity | THEATRE | None | 500+ trades | 52 |
| 6 | Reward Shaping | INVALID | None | N/A | N/A |
| 7 | Setup Fingerprint | THEATRE | None | 500+ trades | 0 |
| 8 | Nightly Activation | PARTIAL | Diagnostic only | N/A | N/A |
| 9 | Meta-Labelling | PARTIAL (disabled) | None (correctly) | 200+ trades | 52 |
| 10 | Walk-Forward | PARTIAL | None | 200+ trades | 52 |
| 11 | SHAP Stability | THEATRE (gated) | None | 4 windows | 0 |

**0/11 components are REAL. 5 THEATRE, 4 PARTIAL, 1 INVALID, 1 correctly disabled.**

---

## SECTION F: ACADEMIC + INSTITUTIONAL RESEARCH CHECK

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
- **Issue**: T-10 lunch block is a narrow fix. No systematic stale-data framework.

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

## SECTION G: PLAN vs CODE TRUTH TABLE

Each row verifies whether the AEGIS plan claim matches code reality.

| # | Plan Claim | Code Reality | Status | Evidence |
|---|-----------|-------------|--------|----------|
| 1 | Confidence floor = 65 (ThresholdRegistry) | `threshold_registry.py:99` = 65, BUT `gate_diagnostics.py:62` = 55, `main.py:2973` = 60 | **CONTRADICTION** | 3 different values |
| 2 | Weekly halt = -8% (circuit breakers) | `circuit_breakers.py:120` = 8%, `risk_sizer.py:52` = 6% | **CONTRADICTION** | 2 different values |
| 3 | Max concurrent positions = 4 | `settings.yaml:623` = 4, `risk_sizer.py:53` = 3, `tick_loop.py:286` = 2 | **CONTRADICTION** | 3 different values |
| 4 | RVOL FAST = 0.30 | `daily_target.py:67` = 0.60, comment: "0.30 was suicidal" | **PARTIAL** | Code deviated with justification |
| 5 | Max signals/day = 4 | `daily_target.py:74` = 3, `gate_diagnostics.py:61` = 1 | **CONTRADICTION** | 3 different values |
| 6 | Lunch penalty = -10 flat | `daily_target.py:530-534` = 0.85x multiplier | **PARTIAL** | Different mechanism |
| 7 | SessionProtection halt = +2.5% | `risk_sizer.py:397` = +2.0% | **PARTIAL** | 50bps below plan |
| 8 | Redis = allkeys-lru | `docker-compose.yml:98` = noeviction | **CONTRADICTION** | Opposite policies |
| 9 | ADX FAST = 15, SLOW = 20 | `daily_target.py:81-83` = 15/20 | **VERIFIED** | Matches plan |
| 10 | ISA SHORT guard | S15: `daily_target.py:1110-1116` ✓, S16: **MISSING** | **PARTIAL** | S16 has no guard |
| 11 | ML disabled (AEGIS 0-05) | `ml_meta_model.py:56` `_ML_ENABLED = False` | **VERIFIED** | Matches plan |
| 12 | Timezone = Europe/London | `settings.yaml:9` = "Europe/London" | **VERIFIED** | Fixed (was US/Eastern) |
| 13 | VIX hysteresis = 5% deadband | `circuit_breakers.py:96` = 15% | **PARTIAL** | More conservative than plan |
| 14 | Kill switch dual implementation | File-based + Redis, both checked | **VERIFIED** | `main.py:1720-1736` |
| 15 | Equity denominator updates daily | `circuit_breakers.py:755-779` fixed. `sheets_logger.py:57` and `dynamic_sizer.py:214` still default 10000 | **PARTIAL** | CB fixed, others not |
| 16 | Consecutive loss query scoped | `main.py:1377-1404` uses ScopedQuery | **VERIFIED** | Fixed |
| 17 | VIX default = 20 (not 0) | Fail-closed at 20 when fetch fails | **VERIFIED** | Fixed |
| 18 | Signal queue = async | `main.py:1276` PrioritySignalQueue(maxsize=50) | **VERIFIED** | Fixed |
| 19 | ISA eligibility enforced | `main.py:1851` filters ISA tickers | **VERIFIED** | Fixed |
| 20 | Regime map = 8 states | `regime_classifier.py:59-61` = 8 states | **VERIFIED** | Matches |
| 21 | Correlation families defined | `dynamic_sizer.py:1302-1313` — US tickers only, ISA `.L` tickers never match | **PARTIAL** | Families exist but wrong universe |
| 22 | CB persistence = Redis | `circuit_breakers.py:393-414` persists to Redis | **VERIFIED** | Fixed |
| 23 | ML feature leakage fixed (J-01) | `ml_meta_model.py:101-103` confidence removed | **VERIFIED** | Fixed |
| 24 | Walk-forward fixed (J-03) | `ml_meta_model.py:491-566` expanding-window | **VERIFIED** | Fixed |
| 25 | Fear & Greed = CNN equities | `cross_asset_macro.py:310` fetches CNN | **VERIFIED** | Comment at line 298 still says "Alternative.me" |
| 26 | Transition buffer exists | `regime_classifier.py:500-505` defined | **PARTIAL** | Defined but `decrement_transition_buffer()` NEVER CALLED |
| 27 | ImmutableRiskRules immutable | Instance `__setattr__` guard exists. Class-level mutation possible. | **PARTIAL** | No metaclass guard |
| 28 | Overnight kill for ALL ETPs | Only 5x may be enforced; 3x unclear | **PARTIAL** | Needs verification |
| 29 | 100-trade validation gate | No executable code exists | **NOT IMPLEMENTED** | Plan-only |
| 30 | Profit ladder = Chandelier only | 3 implementations exist | **CONTRADICTION** | `chandelier_exit.py`, `profit_ladder.py`, VT inline |

**Summary: 14 VERIFIED, 9 PARTIAL, 6 CONTRADICTION, 1 NOT IMPLEMENTED**

---

## SECTION H: STOP-SHIP ITEMS (Top 25)

### P0 — Critical (12 items)

| ID | Title | Blast Radius | Evidence | Fix |
|----|-------|-------------|----------|-----|
| H-01 | Weekly halt contradiction (6% vs 8%) | Risk management integrity | `risk_sizer.py:52` vs `circuit_breakers.py:120` | Decide 6%. Update CB to 0.06. |
| H-02 | Max positions contradiction (3 vs 4 vs 2) | Concentration risk | `risk_sizer.py:53` vs `settings.yaml:623` vs `tick_loop.py:286` | Align to 3 everywhere. |
| H-03 | ImmutableRiskRules class-level mutation | Constitutional controls bypassable | `risk_sizer.py:71-94` no metaclass | Add metaclass `__setattr__`. |
| H-04 | Redis noeviction global | All state persistence fails at 400MB | `docker-compose.yml:98` | Change to volatile-lru + TTL on telemetry. |
| H-05 | Three profit ladder systems | Conflicting exit signals | `chandelier_exit.py`, `profit_ladder.py`, `virtual_trader.py:1666` | Confirm Chandelier as sole. Remove/archive others. |
| H-10 | Signal list mutation during iteration | Silent signal loss | `main.py:1929` (plan ref) | Replace with list comprehension. |
| H-14 | Transition buffer never decremented | Phantom regime states | `regime_classifier.py:500-505` — 0 callers | Call at end of each regime eval cycle. |
| H-15 | No IMAGE_PARITY deploy gate | Stale Docker image deploys | Plan RI-01 | Check git SHA vs image digest at boot. |
| H-16 | SyntheticBroker doesn't exist | Cannot validate execution logic | Plan CR-01 (Phase Q2) | Build when Phase Q2 begins. |
| H-17 | AsyncioHeartbeat doesn't exist | GIL freeze undetected | Plan CR-02 (Phase Q2) | Build basic monitor for Q1. |
| H-20 | InvariantEnforcer status unknown | 12-invariant safety net may be absent | `main.py:1281-1296` blanket except | Verify init + scheduled run. |
| H-21 | 100-trade validation gate not executed | All subsequent work gated | Plan RK-01 | Execute 100 trades post timing fixes. |

### P1 — High (13 items)

| ID | Title | Evidence |
|----|-------|----------|
| H-06 | Equity denominator partial fix | `sheets_logger.py:57`, `dynamic_sizer.py:214` default 10000 |
| H-07 | RVOL FAST deviated from plan | `daily_target.py:67` = 0.60 vs plan 0.30 |
| H-08 | Max signals/day = 3 vs plan's 4 | `daily_target.py:74` |
| H-09 | Lunch multiplier differs from plan | `daily_target.py:530-534` = 0.85x vs -10 |
| H-11 | Stale comment in cross_asset_macro | Line 298 says "Alternative.me" but uses CNN |
| H-12 | VIX hysteresis 15% vs plan's 5% | `circuit_breakers.py:96` |
| H-13 | Monthly halt implemented but not reconciled | `circuit_breakers.py:121` = -15% |
| H-18 | ReconciliationAuditor doesn't exist | Plan CR-03 (Phase Q2) |
| H-19 | Anti-cascade needs test coverage | `circuit_breakers.py:123-126` |
| H-22 | Walk-forward still has StratifiedKFold | `ml_meta_model.py:287-288` (ML disabled) |
| H-23 | IBKR reconnection loop not implemented | Plan GQ-01 |
| H-24 | Monday Go-NoGo guardrail missing | Plan GQ-02 |
| H-25 | Overnight kill not enforced for all ETPs | 3x ETPs may hold overnight |

---

## SECTION I: TIMING/GATING TRIAGE (Sprint-Ready)

### TG-01: TickLoop Silent Failure → Fatal Error
- **Priority**: P0 | **Effort**: 1h | **Dependencies**: None
- **Files**: `main.py:9222-9225`
- **Win-Rate Impact**: Liveness — system opens ZERO trades if TickLoop dies
- **Accept**: TickLoop failure activates kill switch + Telegram alert. `logger.critical`, not `warning`.

### TG-02: Stale Data Age Check
- **Priority**: P0 | **Effort**: 2h | **Dependencies**: None
- **Files**: `tick_loop.py:930-940`
- **Win-Rate Impact**: High — entries on 15-min-old yfinance data are fiction
- **Accept**: After fetch, check `data.index[-1]` vs `now_utc()`. Reject if age > 120s during active session.

### TG-03: Sequential → Parallel yfinance Fetch
- **Priority**: P0 | **Effort**: 4h | **Dependencies**: None
- **Files**: `data_hub/sources/yfinance_source.py`, `data_hub/hub.py`, `signal_engine/engine.py`
- **Win-Rate Impact**: Critical — 12 tickers in ~3-5s instead of 24-72s
- **Accept**: `ThreadPoolExecutor(max_workers=4)`. Total fetch < 4 seconds.

### TG-04: Confidence Floor Unification
- **Priority**: P0 | **Effort**: 3h | **Dependencies**: None
- **Files**: `daily_target.py:75`, `risk_sizer.py:57`, `gate_diagnostics.py:62`, `main.py:2973,4842`
- **Win-Rate Impact**: Medium-High — eliminates nondeterministic filtering
- **Accept**: All modules import from `ThresholdRegistry`. Grep returns zero standalone `_MIN_CONFIDENCE` literals.

### TG-05: Weekly Halt Unification
- **Priority**: P0 | **Effort**: 2h | **Dependencies**: None
- **Files**: `circuit_breakers.py:120`, `risk_sizer.py:52,462`
- **Win-Rate Impact**: Low (risk management, not alpha)
- **Accept**: Single value (6%) everywhere. Invariant test at boot.

### TG-06: SessionProtection +2.0% → +2.5%
- **Priority**: P0 | **Effort**: 0.5h | **Dependencies**: TG-05
- **Files**: `risk_sizer.py:397`
- **Win-Rate Impact**: Medium — removes 50bps upside cap
- **Accept**: `0.02` → `0.025`. Daily P&L of +2.1% does NOT trigger halt.

### TG-07: Signal Queue TTL
- **Priority**: P1 | **Effort**: 2h | **Dependencies**: TG-03
- **Files**: `tick_loop.py:276`, `main.py:1276`
- **Win-Rate Impact**: Medium — prevents execution on stale signals
- **Accept**: Signals carry `created_at` timestamp. Reject if > 30s old.

### TG-08: IBKR Reconnection Loop
- **Priority**: P1 | **Effort**: 3h | **Dependencies**: None
- **Files**: `tick_loop.py:310-320`, `ibkr_source.py`
- **Win-Rate Impact**: Medium — prevents permanent yfinance fallback
- **Accept**: TickLoop checks `ibkr_client.connected` every 60s. Auto-reconnect on disconnect.

### TG-09: S16 ISA SHORT Guard
- **Priority**: P0 | **Effort**: 1h | **Dependencies**: None
- **Files**: `universal_scanner.py:473,527,579,635,692`, `main.py:4841-4845`
- **Win-Rate Impact**: None (legality issue — UK ISA prohibits short selling)
- **Accept**: SHORT signal for ISA ticker → rejected. Inverse ETPs exempt.

### TG-10: Redis volatile-lru Migration
- **Priority**: P0 | **Effort**: 2h | **Dependencies**: None
- **Files**: `docker-compose.yml:98`, `core/redis_config.py`
- **Win-Rate Impact**: None directly — prevents state persistence failure
- **Accept**: `noeviction` → `volatile-lru`. DB 1 telemetry keys get 1h TTL. DB 0 keys persist.

### TG-11: Dual Signal Generator Consolidation
- **Priority**: P1 | **Effort**: 8h | **Dependencies**: TG-01, TG-03, TG-07
- **Files**: `main.py` (scheduler), `tick_loop.py` (Brain)
- **Win-Rate Impact**: Medium — eliminates duplicate/contradictory signals
- **Accept**: Single signal bus. Deduplication by (ticker, direction, strategy, 60s window).

### TG-12: Max Concurrent Positions Reconciliation
- **Priority**: P0 | **Effort**: 0.5h | **Dependencies**: None
- **Files**: `risk_sizer.py:53`, `settings.yaml:623`, `tick_loop.py:286`
- **Win-Rate Impact**: Low — prevents over-concentration
- **Accept**: Single value (3) in all three files. Invariant test at boot.

---

## SECTION J: PLAN PATCHES

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

---

## SECTION K: MINIMUM VIABLE SYSTEM

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

---

## CRITICAL QUESTIONS

**The questions this system cannot yet answer:**

1. **What is the actual signal-to-fill latency?** No metric tracks elapsed time from signal creation to `open_position()`. The 30-84s estimate is theoretical.

2. **Which profit ladder actually closes trades?** Three exist. Which one fired on each of the 52 losses?

3. **What is the actual yfinance data age at execution?** Fetch latency is checked, data timestamp is not. Were entries based on 15-min-old prices?

4. **Does InvariantEnforcer actually run?** Initialized with blanket except (`main.py:1296`). Any log evidence of 60s execution?

5. **What regime was active during each of the 52 trades?** Transition buffer never decrements — were trades entered during phantom states?

6. **What is ISA ETP correlation?** `dynamic_sizer.py:1302-1313` has US tickers only. ISA `.L` tickers never match. Correlation penalty has NEVER fired.

7. **What spread did each trade pay?** VirtualTrader uses proxy spread model. Real LSE ETP spreads vary 10-50bps intraday.

8. **Can +2.0% daily net actually be reached after costs?** SessionProtection halts at +2.0% gross. With 3 trades at 15-25bps round-trip, max achievable net is ~1.25-1.55%.

9. **What happens when Redis hits 400MB?** Does Chandelier exit handle write failures? Does circuit breaker state survive?

10. **Is 0.75% risk-per-trade appropriate for 3x ETPs?** 0.75% on 3x means underlying moves 0.25% to stop. Entry slippage alone can be 10-25bps (40-100% of risk budget).

11. **What was the confidence score distribution of the 52 losers?** If median confidence was 70+, the scoring system is fundamentally miscalibrated.

12. **Can APScheduler and TickLoop produce contradictory signals?** Both run independently. No deduplication exists.

13. **What is max drawdown from 3 concurrent 3x ETPs reversing?** Nominal max = 2.25%. With gap risk on 3x, actual could be 5-7%.

---

## IF I HAD 8 HOURS TODAY

### Hour 1: Kill the Silent Failures
- **Files**: `main.py:9222-9225`, `tick_loop.py:381-393`
- **Deliverable**: TickLoop failure → kill switch + Telegram alert + `logger.critical`
- **Accept**: Mock TickLoop.start() to raise → kill switch activates within 60s

### Hour 2: Unify Confidence Floors
- **Files**: `daily_target.py:75`, `risk_sizer.py:57`, `gate_diagnostics.py:62`, `main.py:2973,4842`
- **Deliverable**: Single floor = 65 from ThresholdRegistry
- **Accept**: `grep -rn "_MIN_CONFIDENCE"` returns ZERO standalone literals

### Hour 3: Unify Weekly Halt + Max Positions + SessionProtection
- **Files**: `circuit_breakers.py:120`, `risk_sizer.py:397`, `settings.yaml:623`, `tick_loop.py:286`
- **Deliverable**: Weekly = 6%. Max positions = 3. SessionProtection = +2.5%.
- **Accept**: Boot with zero invariant failures

### Hour 4: Data Age Check
- **Files**: `tick_loop.py:930-940`
- **Deliverable**: Reject data where last bar > 120s old during active session
- **Accept**: Mock stale yfinance → `_get_fresh_price()` returns 0.0

### Hour 5: S16 ISA SHORT Guard + Transition Buffer Fix
- **Files**: `main.py:4841`, `universal_scanner.py`, `regime_classifier.py:500`
- **Deliverable**: S16 SHORTs rejected for ISA tickers. Transition buffer decrements.
- **Accept**: SHORT on QQQ3.L → rejected. Regime change → buffer counts to 0.

### Hour 6: Redis + ImmutableRiskRules
- **Files**: `docker-compose.yml:98`, `risk_sizer.py:31-69`
- **Deliverable**: Redis volatile-lru. ImmutableRiskRules raises on class-level mutation.
- **Accept**: Redis CONFIG GET = volatile-lru. `ImmutableRiskRules.RISK_PER_TRADE = X` → AttributeError.

### Hour 7: Parallel yfinance Fetch
- **Files**: `data_hub/hub.py`, `data_hub/sources/yfinance_source.py`
- **Deliverable**: 12-ticker fetch in <4s (was ~24s)
- **Accept**: Timing log shows batch fetch < 4000ms

### Hour 8: Deploy + Verify + Document
- **Files**: All modified files, `deploy.sh`
- **Deliverable**: System running on EC2 with all fixes. First tick produces signals with correct thresholds.
- **Steps**: `pytest` → `python -c "import main"` → deploy → verify docker logs → wait for one tick cycle → commit

**End state**: System loses trades for the RIGHT reasons (bad signals) not WRONG reasons (silent failures, contradictory thresholds, stale data, bricked Redis). The 100-trade validation gate can begin.

---

**END OF AUDIT REPORT**

*Generated: 2026-03-08 by NZT-48/AEGIS Full-Spectrum Institutional Audit*
*Auditor: Claude Opus 4.6 (4 personas, 11 sections, ~25 subsystems)*
