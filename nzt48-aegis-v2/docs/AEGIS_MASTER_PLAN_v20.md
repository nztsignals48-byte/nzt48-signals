# AEGIS V2 — MASTER PLAN v20
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 20.0 | **Date**: 2026-03-09 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v19. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v19.md — Claude's independent 200-bullet adversarial audit + full Gemini 200-bullet triage. Fixes are marked **[v20-FIX-N]** for traceability. There are 14 v20 fixes addressing the most critical issues found in the combined adversarial audit.

---

## v20 DELTA — 14 IMPLEMENTATION TRAP FIXES

| Fix | Trap | What was wrong in v19 | What v20 does |
|-----|------|-----------------------|---------------|
| **v20-FIX-1** | Docker SIGKILL at 10s kills SIGTERM handler | SC-01 waits 30s for fills but Docker defaults to SIGKILL after 10s. Every production restart corrupts WAL. Not fixed in any v19 phase. | Add `stop_grace_period: 60s` to `docker-compose.yml` as SC-01a — explicit Phase 8 deliverable alongside SC-01. |
| **v20-FIX-2** | Polars vCPU starvation → IBKR disconnect | Ouroboros Polars `LazyFrame.collect()` consumes 100% of both c7i-flex.large vCPUs. Tokio async reactor starved. IBKR WebSocket drops. Carry positions lose monitoring connection. | Add `POLARS_MAX_THREADS=2` as environment variable in `docker-compose.yml` (Phase 8, SC-13). |
| **v20-FIX-3** | Half-Kelly + Min Entry = 0 trades possible | `kelly_fraction.clamp(0.0, 0.20)` confirmed in `types/structs.rs:132`. Half-Kelly clips to 10% max. At £10,000 equity × 10% = £1,000 < £1,500 minimum entry. System cannot place ANY trade during the entire paper phase. Crucible 100-trade gate physically impossible. | Replace binary Half-Kelly switch with dynamic Kelly ramp: `kelly_scale = max(0.1, min(1.0, validated_trades / 250))`. Floor of 0.1× ensures the system can actually place trades from day 1 (£10k × 0.1 × max_kelly = £200 base, sufficient for paper). At 0 trades: 0.1× Kelly + **min-entry gate suspended** until ramp reaches 1.0. At 125 trades: 0.5×. At 250: 1.0×. |
| **v20-FIX-4** | WAL compaction severs open position history | Phase 22 30-day rolling WAL compaction deletes entry event for a 31-day mega-runner carry position. WAL replay on restart cannot reconstruct the position. Engine treats it as orphan → force-liquidates most profitable position. | WAL compaction job MUST exclude all events where `ticker_id` appears in `portfolio.rs::positions` (open positions) before deleting. Phase 22 amendment. |
| **v20-FIX-5** | reqPnLSingle — IBKR 1-per-connection limit | Phase 20 calls `req_pnl_single(pnl_req_id, account, "", conid)` per carry position. IBKR allows exactly 1 PnL subscription per connection. Subscriptions 2-6 silently rejected (Error 10197). Five of six carry positions unmonitored for overnight gap risk. | Replace all `reqPnLSingle` with account-level `reqPnL(pnl_req_id, account, "")`. Receives PnL updates for ALL positions in one stream. Uses 1 subscription regardless of carry count. Phase 20 amendment. |
| **v20-FIX-6** | clock.rs BST overflow at 23:30 UTC | `(utc_secs_from_midnight + 3600)` without `% 86400` produces 88200 at 23:30 UTC. Mode boundary checks receive invalid seconds value, silently passing wrong mode for all boundary checks. Also: `day_of_year = (epoch_secs / 86400) % 365` is wrong for leap years. | Replace `clock.rs` BST logic with `chrono-tz` crate's `Europe__London` timezone. Eliminates both the overflow bug and the leap-year day-of-year approximation simultaneously. Phase 11 amendment. |
| **v20-FIX-7** | tokio::sync::Mutex required in async context | Phase 8 adds Tokio async runtime. Phase 11 `SubscriptionManager` uses `std::sync::Mutex`. In Tokio async context, `std::sync::Mutex` held across `.await` points deadlocks the entire runtime. All async lock holders block the Tokio reactor. | SubscriptionManager must use `tokio::sync::Mutex` from Phase 8 onward. SC-02 amended to specify `tokio::sync::Mutex` explicitly. All future Rust modules in async context must use `tokio::sync::Mutex`. |
| **v20-FIX-8** | No reqMarketDataType(3) call in broker | `ibkr_broker.rs::connect()` makes no market data type declaration. In paper mode without live data subscriptions, IBKR defaults to live data type, which requires paid market data subscriptions. All ticks return empty or delayed data silently. | Add `reqMarketDataType(3)` as the **first API call** in `ibkr_broker.rs::connect()` before any subscriptions. Phase 8 deliverable (SC-14). |
| **v20-FIX-9** | Heartbeat only fires during DARK (22h gap) | Phase 17 heartbeat is "Ouroboros step 9" — fires once per night during DARK mode. Watchdog checking for missed heartbeats fires constant false positive alerts for the other 22 hours per day when the system is actively trading. | Move heartbeat to `engine.rs` main loop: write `aegis_heartbeat_ts` to Redis via `SETEX` every 30 minutes from Rust. Decouple entirely from Ouroboros. Ouroboros step 9 sends Telegram 🟡 ALIVE message only. Phase 17 amendment. |
| **v20-FIX-10** | RotationScanner StrategyId absent from enums.rs | `types/enums.rs` `StrategyId` enum only has `VanguardSniper` and `ApexScout`. No `HotScanner` or `RotationScanner` variants. Thompson Sampling posteriors updated from WAL outcomes keyed by StrategyId — attribution is impossible without the enum variants. | Add `StrategyId::HotScanner` and `StrategyId::RotationScanner` to `types/enums.rs` as a Phase 8 pre-condition deliverable (SC-15). |
| **v20-FIX-11** | Beta-Bernoulli Thompson Sampler EV blindness | Asset with 9 wins of +1% + 1 loss of -20% has WR=90% but EV=-1.1%. Beta-Bernoulli bandit scores it ~0.90 and aggressively allocates lines to it. Negative-EV assets become most favored. Confirmed independently by both Claude and Gemini. | Replace Beta-Bernoulli Thompson Sampler with **Gaussian-Gaussian** (Normal-Normal) conjugate model. Reward = continuous PnL% (not binary win/loss). Posterior: `μ_posterior = (μ_0/σ_0² + Σr_i/σ²) / (1/σ_0² + n/σ²)`. Phase 13 amendment. |
| **v20-FIX-12** | DCC-GARCH 5-min blind during flash crash | During flash crash, correlations jump to 1.0 within milliseconds. The 5-minute cached DCC-GARCH matrix from before the crash shows low correlations. CVaR heat limit stays wide. Risk arbiter approves entries into structurally broken market. | Add VIX circuit breaker: if `VIX_change_1min > 10%` → immediately invalidate DCC-GARCH cache → CVaR uses conservative max-correlation matrix (all ρ=1.0) until cache refreshed. Phase 15 amendment. |
| **v20-FIX-13** | Crossbeam drop-oldest corrupts Chandelier H/L | Buffer overflow at US open drops oldest ticks. `MarketTick` High/Low used by Chandelier ratchet are permanently lost for those bars. Chandelier computes on incomplete OHLCV — stop levels systematically understated. | On `TrySendError::Full`: instead of dropping the oldest tick, **aggregate it into the current bar** — update current bar's H/L/V with the dropped tick's values. No tick data lost. Phase 8 amendment (SC-09). |
| **v20-FIX-14** | Carry line allocator wrong — assumes 3 not 6 | `MAX_CARRY=6` but the allocator in Phase 20 computes `available = 100 - fixed_carry_lines` assuming max 3 carry positions = 6 lines. With 6 actual carry positions = 12 lines consumed, the formula over-allocates scanner lines by 6. Line budget invariant violated at carry cap. | Fix allocator formula: `available = 100 - (current_carry_count × 2) - active_scanner_lines`. Dynamic, not static. Phase 20 amendment. |

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

| Component | Status | Critical Issues |
|-----------|--------|----------------|
| **S15 daily_target.py** | ACTIVE | T-01→T-08 timing tuning applied; 0% win rate on 52 paper trades — root cause is execution timing, not signal quality |
| **S3 mean_reversion.py** | DORMANT | Hard ETP veto (Avellaneda 2010) is correct code; V2.1 reactivation comment contradicts it — remove comment (SC-07) |
| **chandelier_exit.py** | ACTIVE | VirtualTrader inline ladders disabled; Le Beau 5-rung working; Redis-persisted (7-day TTL) |
| **cross_asset_macro.py** | ACTIVE | C-06 fixed: weekly HMM refit + 3-tick confirmation buffer; VIX 5-min cache (not 30-min) |
| **ml_meta_model.py** | DISABLED | AEGIS 0-05: regime encoding always -1; confidence leaked as input feature (circular feedback); 43.7% training data fabricated |
| **uk_isa/ (15 files)** | ACTIVE | 12 leveraged ETPs active; lse_registry auto-scraper; correlation_engine; predictive_scoring |
| **sprint6_live_gate.py** | NOT MET | 0% WR / 52 paper trades; all 10 Romano-Wolf criteria fail; need 63+ MTRL days |
| **state_manager.py** | ACTIVE | Redis SSOT V8.0 with Lua atomicity |
| **startup_gate.py** | ACTIVE | 8 pre-flight checks (H-01) |
| **invariant_enforcer.py** | ACTIVE | 12 runtime invariants (H-02) |
| **profit_ladder.py** | SUPERSEDED | Chandelier is sole exit authority; V3 inline ladders disabled |
| **quant_math/ (14 files)** | DORMANT | Almgren-Chriss, Hawkes, fractional diff — Phase Q2+ |

**V1 Critical Bugs (must fix before or during V2 migration):**

| ID | Severity | Module | Issue | Fix |
|----|----------|--------|-------|-----|
| **AEGIS 0-05** | CRITICAL | ml_meta_model.py | Regime encoding always returns -1; confidence circular feedback; 43.7% fabricated data | Disable entirely until J-01/J-02 fixed and N ≥ 200 real trades |
| **J-02** | CRITICAL | ml_meta_model.py | Regime map uses fictional keys ("bull"/"bear") not RegimeState enum | Remap to actual RegimeState enum values |
| **J-01** | CRITICAL | ml_meta_model.py | Confidence leaked as ML input feature | Remove confidence; replace with raw_indicator_count, spread_bps, time_since_regime_change |
| **S3 Contradiction** | LOW | mean_reversion.py | Hard veto on leveraged ETPs (correct) contradicted by V2.1 reactivation comment (wrong) | Remove reactivation comment in SC-07 |

---

### 1.2 V2 Rust Engine — Complete Module Inventory

**Status: Phases 1-7 COMPLETE. ~9,000 LOC. 147+ tests. All 98 P0+P1 stop-ship items resolved.**

| Module | LOC | Tests | Status |
|--------|-----|-------|--------|
| engine.rs | 700+ | 12 | COMPLETE |
| ibkr_broker.rs | 400+ | 9 | COMPLETE — uses `subscribe_bars()` (5s OHLCV); Phase 11 migrates to tick-by-tick `reqMktData` |
| paper_broker.rs | 400+ | implicit | COMPLETE |
| risk_arbiter.rs | 255 | 22 | COMPLETE — 22-check gate, 4-regime hierarchy, fail-closed; missing MINIMUM_ENTRY_GBP (added Phase 8) |
| exit_engine.rs | 303 | 22 | COMPLETE — 5-rung Chandelier, shadow stops (H67), ratchet enforcer |
| portfolio.rs | 251 | 9 | COMPLETE — position tracking, heat calc, sector/inverse metadata |
| python_bridge.rs | 204 | implicit | COMPLETE — JSON-lines subprocess IPC; synchronous per-tick |
| wal_writer.rs + wal_replay.rs | 480+ | 36 | COMPLETE — CRC32, disk-space check (H25), dead-letter |
| reconciler.rs | 245+ | implicit | COMPLETE — orphan detection, position matching |
| universe.rs | 200+ | 14 | COMPLETE — Vanguard/Apex routing, filter chain |
| clock.rs | 250+ | implicit | COMPLETE — BST BUGS CONFIRMED (v20-FIX-6: chrono-tz required) |
| config_loader.rs | 370+ | implicit | COMPLETE — 4-TOML load, validated at startup |
| types/ (4 files) | 1000+ | 4 | COMPLETE — 10 enums, 2 newtypes, MarketTick, OrderIntent; missing HotScanner/RotationScanner (v20-FIX-10) |
| ouroboros_loader.rs | 225+ | implicit | COMPLETE — nightly artifact load |
| channel.rs | 150+ | implicit | COMPLETE — tick backpressure, circular buffer, capacity=50,000 confirmed |
| **TOTAL** | **~9,000** | **147+** | **COMPLETE** |

**V2 Confirmed Facts:**
- Paper mode hardcoded: `IS_LIVE = false` in main.rs:26
- IB Gateway port: **4004** (NOT 4002)
- client_id = 101 (V1 uses 100)
- Ouroboros nightly via Supercronic crontab in container
- IBKR reconnect + BackoffState (5 attempts, exponential) implemented — **must extend to 20 attempts for 04:45 UTC 3-min GW restart window (P2-14)**
- Zero panics: `#![deny(clippy::unwrap_used)]` + `#![deny(warnings)]`

**V2 Deferred by Design (new phase assignments):**

| Component | Phase |
|-----------|-------|
| tokio async reactor | Phase 8+ |
| Multi-ticker SubscriptionManager + line budget | Phase 11 |
| 5-mode clock + ModeController (chrono-tz) | Phase 11 |
| Infinite Chandelier 8-multiplier | Phase 14 |
| ADV-bounded execution + U-shaped TWAP | Phase 14 |
| RiskGate 31-veto expansion | Phase 15 |
| CVaR heat with Cornish-Fisher | Phase 15 |
| Polars Ouroboros pipeline | Phase 16 |
| External bulk EOD data provider | Phase 16 |
| Telegram long-polling + PyMuPDF PDFs | Phase 17 |
| European exchange profiles (15 exchanges) | Phase 18 |
| Asian exchange profiles + KRX VI + NZX | Phase 19 |
| Overnight carry state machine + HALTED state | Phase 20 |

---

## PART 2 — GEMINI + CLAUDE ADVERSARIAL AUDIT — COMBINED TRIAGE SUMMARY

Both Gemini (200 bullets) and Claude (200 bullets) independently produced adversarial audits. This section summarizes the combined dispositions; all accepted findings are injected into Part 3 phase deliverables.

### 2.1 Top Priority Fixes (Combined P0 + P1 matrix from AEGIS_SELF_ANALYSIS_TRIAGE_v19.md)

**P0 — Fatal (System Will Not Function):**

| ID | Issue | v20 Fix | Phase |
|----|-------|---------|-------|
| P0-1 | Docker SIGKILL at 10s vs 30s SIGTERM wait | `stop_grace_period: 60s` in docker-compose.yml | **v20-FIX-1, Phase 8** |
| P0-2 | Polars vCPU starvation → IBKR disconnect | `POLARS_MAX_THREADS=2` in docker-compose.yml | **v20-FIX-2, Phase 8** |
| P0-3 | Half-Kelly + Min Entry = 0 trades possible | Dynamic Kelly ramp: floor 0.1× at 0 trades, scales linearly to 1.0× at 250; min-entry gate suspended during ramp | **v20-FIX-3, Phase 8/15** |
| P0-4 | WAL compaction severs open position history | Exclude open position events from compaction | **v20-FIX-4, Phase 22** |
| P0-5 | reqPnL 1-per-connection IBKR limit | Account-level reqPnL instead of reqPnLSingle | **v20-FIX-5, Phase 20** |
| P0-6 | clock.rs BST addition missing % 86400 | chrono-tz Europe::London | **v20-FIX-6, Phase 11** |
| P0-7 | tokio::sync::Mutex required in async context | Replace std::sync::Mutex in SubscriptionManager | **v20-FIX-7, Phase 8** |
| P0-8 | No reqMarketDataType(3) call in broker | Add as first call in ibkr_broker.rs::connect() | **v20-FIX-8, Phase 8** |
| P0-9 | Heartbeat only fires in DARK (22h gap) | Engine-side 30-min heartbeat Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent from WAL | Add HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |

**P1 — High (System Will Fail in Common Conditions):**

| ID | Issue | v20 Fix | Phase |
|----|-------|---------|-------|
| P1-1 | snapshot=True blocks 11s on illiquid | `tokio::timeout(200ms)` + ETP fallback | Phase 12 |
| P1-2 | Telegram polling thread dies silently | Infinite retry loop with exponential backoff | Phase 17 |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker cache invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV allocation | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | Drop-oldest corrupts Chandelier H/L | Aggregate H/L/V on overflow instead of drop | **v20-FIX-13, Phase 8** |
| P1-6 | Cost basis wrong after overnight split | Nightly clear + IBKR reqPositions resync | Phase 8 |
| P1-7 | Dust market-sell slippage on illiquid | Peg-to-Mid limit, 3min TIF | Phase 8 |
| P1-8 | AtomicUsize leaks on dropped ACK | 5-min periodic IBKR reconciliation | Phase 11 |
| P1-9 | FTT intraday exemption lost on carry | Flag FTT entries as no-carry eligible | Phase 18/20 |
| P1-10 | NZX misses opening auction daily | Pre-subscribe NZX at 22:55 UTC in DARK | Phase 19 |
| P1-11 | ISA tax year Jan 1 vs April 6 | Fix isa_gate.rs boundary to April 6 | Phase 12 |
| P1-12 | HKEX board lot → 0-share order | Fallback to ETP when lot×price > Kelly | Phase 12 |
| P1-13 | Polars parallel step execution → OOM | Enforce sequential step execution | Phase 16 |
| P1-14 | ibkr_broker.rs uses bar subscription not ticks | Clarify/migrate subscription type in Phase 11 | Phase 11 |
| P1-15 | Carry allocator wrong — assumes 3 not 6 | Dynamic: available = 100 − (carry_count × 2) | **v20-FIX-14, Phase 20** |

### 2.2 Binding Architectural Mandates (Retained from v19 + v20 additions)

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Use Polars `LazyFrame` + Arrow zero-copy. 500-ticker batches. RSS ceiling 3.5GB. | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** `crossbeam-channel` bounded (capacity=50,000). Overflow → aggregate H/L/V into current bar, NOT drop oldest tick **(v20-FIX-13)**. | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox fix.** IBKR `reqHistoricalData` token bucket (60 req/10min) for active ~100 tickers ONLY. Nightly 5,000+ ticker universe → Polygon.io/Databento. | Phase 8 + 16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed ONLY when live position exists. HotScanner/RotationScanner candidates do NOT get underlyings tracked. | Phase 11 |
| **GEM-A5** | **Drawdown tier nomenclature.** Yellow = Kelly × dynamic_ramp, no new entries. Orange = close all positions. Red = full halt. Ouroboros failure → Yellow. | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time calculations via `chrono_tz::Europe::London`, not manual approximation. Eliminates BST leap-year bug and overflow bug. | Phase 11 |
| **v20-A2** | **tokio::sync::Mutex everywhere.** Any Mutex held across `.await` MUST be `tokio::sync::Mutex`. Enforced via Clippy lint check in CI. | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler.** Continuous PnL% reward, not binary win/loss. Log-Normal priors for asymmetric return distributions. | Phase 13 |
| **v20-A4** | **Account-level reqPnL only.** Never use `reqPnLSingle` for per-position carry monitoring. One account-level subscription covers all positions. | Phase 20 |

### 2.3 Phase-by-Phase v20 Injections (delta from v19)

**Phase 8 (v20 additions):** SC-13 (`POLARS_MAX_THREADS=2` + dynamic Kelly ramp + min-entry gate suspension), SC-14 (`reqMarketDataType(3)` first call in connect()), SC-15 (StrategyId enum extension), `stop_grace_period: 60s` as explicit SC-01a, SC-09 amended (aggregate H/L/V on overflow), SC-06 amended (Peg-to-Mid + 3min TIF for dust), SC-12 amended (add reverse mapping Polygon→IBKR + preferred share rule), SC-10 amended (CostBasisEntry VWAP struct + nightly clear/resync)

**Phase 11 (v20 additions):** clock.rs replaces BST approximation with chrono-tz (v20-FIX-6), SubscriptionManager uses tokio::sync::Mutex (v20-FIX-7), periodic AtomicUsize reconciliation vs IBKR every 5 min, NZX pre-subscribe at 22:55 UTC during DARK, `ibkr_broker.rs` bar→tick migration clarified, mode_b_plus_end_utc() output specified as UTC seconds (Rust converts)

**Phase 12 (v20 additions):** snapshot=True + `tokio::timeout(200ms)` + ETP fallback on timeout, HKEX board lot ETP fallback, ISA tax year boundary = April 6, ETP 30-day tracking error check (demote if >5%), FTT market cap ±10% hysteresis band, snapshot queue max 5 concurrent (rate limited), integer shares: `floor(kelly_gbp / lot_price_gbp)`

**Phase 13 (v20 additions):** Gaussian-Gaussian Thompson Sampler (v20-FIX-11), StrategyId::HotScanner/RotationScanner wired into WAL attribution, hard slot limit on RotationScanner (max 50 slots), OFI renamed "QuoteImbalance" in code (academically accurate)

**Phase 14 (v20 additions):** Chandelier floor uses `1.5 × spread × leverage_factor` (not flat 1.5× for 3x ETPs), TWAP abort on early_close detection, U-shaped TWAP uses 5-min trailing volume from current session only (not pre-market)

**Phase 15 (v20 additions):** VIX circuit breaker invalidates DCC-GARCH cache on +10% 1-min spike (v20-FIX-12), dynamic Kelly ramp fully specified (0→250 trades linear), min-entry gate behavior at low ramp defined, Cornish-Fisher gated: min N=20 observations + |S|<2 check before CF expansion, min-entry gate suspended in RED tier recovery

**Phase 16 (v20 additions):** `POLARS_MAX_THREADS=2` explicit in docker-compose.yml (v20-FIX-2), atomic write for corp_action_blocklist.json (write to .tmp, validate, rename()), sequential Ouroboros step enforcement (no concurrent steps), `/tmp` Parquet cleanup after each step, use `resource.getrlimit` instead of psutil for RSS pre-allocation check, validate LazyFrame plan at import with `.explain()`

**Phase 17 (v20 additions):** Engine-side heartbeat every 30 min (v20-FIX-9) replaces Ouroboros-only heartbeat, Telegram polling thread infinite retry loop (P1-2), shadow book divergence threshold raised to £50 (or 0.5% of position), watchdog redundancy: EC2 + personal phone cron, `asyncio` event loop isolation for GIL contention

**Phase 18 (v20 additions):** FTT TOML rates stored as integer bps (not floating-point), per-exchange stamp duty map in transaction_tax.toml, FTT intraday exemption + no-carry flag (v20-FIX-14 interaction), VPIN NaN guard (min 5 days ADV data before VPIN enabled), `IDEALPRO` routing enforced for FX pairs in currency.rs, `Decimal` crate for tick-size rounding on Euronext

**Phase 19 (v20 additions):** NZX pre-subscribe at 22:55 UTC during DARK (not 23:00 UTC — arrives ready for open), ASX DST dynamic: query `ZoneInfo("Australia/Sydney").utcoffset()` at Ouroboros init, IBKR reconnect max_attempts = 20 (total backoff ~5min, covers 3-min GW restart), KRX VI: poll `reqContractDetails` to confirm VI cleared (not just timer)

**Phase 20 (v20 additions):** Account-level `reqPnL` (v20-FIX-5), dynamic carry allocator formula (v20-FIX-14), holiday-aware HALTED day counter (trading days, not calendar days), Day 3 market order submitted at exchange open time (not UTC midnight), IBKR margin check during MONITORED holiday state

**Phase 21 (v20 additions):** DCC-GARCH step 10 artifact validation check (if step 9 DCC-GARCH update completes but step 10 artifact write fails → stale JSON detected via timestamp), ES futures via IBKR tick for real-time US sentiment (not Ouroboros)

**Phase 22 (v20 additions):** WAL compaction open-position exclusion (v20-FIX-4), NTP sync check added to startup gate (G-46 accepted), `ArcSwap` for config state instead of RwLock (SIGHUP deadlock prevention), delayed data detection: monitor `reqMarketDataType` response, Prometheus `/metrics` on localhost only (no public exposure), `/tmp` size not restricted in Docker (remove tmpfs limit or set to 256m+)

**Phase 23 (v20 additions):** Romano-Wolf Crucible Suite 1: use single-hypothesis t-test (N=1, not N=20 Bonferroni) since only S15 is under test, Bootstrap resampling (1,000 iterations) for non-parametric WR/Sharpe confidence intervals

### 2.4 Deferred (Post-Crucible)

| Finding | Reason |
|---------|--------|
| Multi-level OFI (5-level depth) | Requires IBKR Level 2 subscription |
| Garman-Klass volatility estimator | Enhancement, not blocking |
| Gaussian Process Regressor for ranking | Phase Q2+ ML |
| Dynamic Time Warping cross-timezone | Phase Q2+ signal research |
| EVT (Extreme Value Theory) CVaR | Enhancement over Cornish-Fisher |
| Contextual Bandits Allocator | Phase Q2+ |
| QuestDB/InfluxDB tick storage | Post-live infrastructure |
| HSMM (Semi-Markov) regimes | Phase Q2+ |
| LULD pause handling (US equities) | Phase 14 follow-up |
| Wash trade AML filter | Compliance review needed |
| Log-Normal Thompson Sampler | Enhancement over Gaussian-Gaussian |
| Markov-Switching DCC | Phase Q2+ |
| ASER weight optimization (ML-derived) | Phase Q2+ |
| Realized GARCH (Hansen 2012) | Phase Q2+ |
| KRX VI post-momentum exploit | Phase Q2+ alpha research |

---

## PART 3 — PHASE PLAN

### Numbering Convention

- **Phases 1-7**: COMPLETE (V2 Rust core)
- **Phase 8**: Pre-conditions and P0 hardening (NEXT) — **expanded from 12 to 15 SC items**
- **Phases 9-10**: Reserved for future use
- **Phases 11-23**: Granular build phases (multi-universe expansion + Crucible)

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 40h | **Status**: NEXT — must complete before Phase 11
*(+6h vs v19 for SC-13/14/15 additions and v20-FIX amendments)*

**Rationale**: 15 SC items now. SC-01 through SC-12 carried from v19. SC-13 (Polars env + dynamic Kelly ramp), SC-14 (reqMarketDataType), SC-15 (StrategyId enum extension) are new. Three v19 SC amendments (SC-06, SC-09, SC-10) updated. None of Phases 11+ can proceed without all 15 items complete.

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: flatten → 30s fill wait → WAL shutdown event → exit | main.rs | — |
| **SC-01a** | `stop_grace_period: 60s` added to docker-compose.yml **(v20-FIX-1)** | docker-compose.yml | v20-FIX-1 |
| SC-02 | SubscriptionManager skeleton: **`tokio::sync::Mutex`**-guarded **(v20-FIX-7)**, deterministic cancel→ACK→subscribe (NOT 2s silence) | subscription_manager.rs | v20-FIX-7 |
| SC-03 | LineBudget struct `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket (60 req/10min, 6 concurrent, Error 162 backoff) for active ~100 tickers; Polygon.io/Databento for nightly 5,000+ universe; single Rust token bucket, separate Python Ouroboros bucket **(G-32)** | ibkr_broker.rs + ouroboros/data_fetch.py | GEM-A3, G-32 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` pre-entry gate in risk_arbiter.rs — **suspended during dynamic Kelly ramp below 250 trades** **(v20-FIX-3)** | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard: **if `filled_gbp < 500.0` → submit Peg-to-Mid limit order (not market-sell) at mid-price, TIF=3min; if not filled in 3min → submit market-sell; cancel unfilled remainder separately** **(v20-FIX-1 Peg-to-Mid + G-P5)** | exit_engine.rs | v19-FIX-1 + G-P5 |
| SC-07 | Fix V1 S3 contradiction: remove conflicting reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler timezone audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| SC-09 | `crossbeam-channel` bounded ring buffer (capacity=50,000); on `TrySendError::Full` → **aggregate dropped tick into current bar: update bar.high=max(bar.high, tick.last), bar.low=min(bar.low, tick.last), bar.volume+=tick.volume** (NOT drop oldest tick) **(v20-FIX-13)** | python_bridge.rs + channel.rs | GEM-A2 + v20-FIX-13 |
| SC-10 | Internal cost-basis tracker: `HashMap<TickerId, CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }>`. VWAP cost basis: `avg_cost = total_cost / total_shares`. Updated on every `OrderFilled` WAL event. **Nightly clear + IBKR reqPositions resync at Ouroboros step 1** **(G-P8, G-17)** | portfolio.rs | G-09, G-P8, G-17 |
| SC-11 | SubscriptionManager `active_line_count: AtomicUsize`; increment on `reqMktData` ACK, decrement on `cancelMktData` ACK; `assert!(count <= 100)` before every new subscription | subscription_manager.rs | G-02 |
| SC-12 | `symbology_mapper.py` (NEW): rules: (a) IBKR space → Polygon dot; (b) IBKR LSE suffix → Polygon prefix; (c) IBKR exchange-prefixed pass-through; (d) preferred shares: `BAC PR D` → `BAC/PD` **(G-7)**; (e) **reverse mapping `to_ibkr(polygon_symbol)` for Universe scan results** **(P2-3)** | ouroboros/symbology_mapper.py | v19-FIX-2 + G-7 + P2-3 |
| **SC-13** | **Dynamic Kelly ramp (NEW, v20-FIX-3):** Replace Half-Kelly binary switch with `kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))`. Floor of 0.1× ensures trades are possible from day 1 (£10k × 10% max-kelly × 0.1 ramp = £100 base — min-entry gate suspended during ramp anyway). Ramp reaches 1.0× at trade 250. Add `POLARS_MAX_THREADS=2` to container environment in docker-compose.yml. `SplitAdjustment` WAL event added: reset Chandelier stop + cost basis on stock split detection **(G-59)** | risk_arbiter.rs + docker-compose.yml + types/wal.rs | v20-FIX-3, v20-FIX-2, G-59 |
| **SC-14** | **`reqMarketDataType(3)` first call (NEW, v20-FIX-8):** `client.req_market_data_type(3)` added as the FIRST API call in `ibkr_broker.rs::connect()`, before any `subscribe_bars()` or `reqMktData` calls. | ibkr_broker.rs | v20-FIX-8 |
| **SC-15** | **StrategyId enum extension (NEW, v20-FIX-10):** Add `StrategyId::HotScanner` and `StrategyId::RotationScanner` variants to `types/enums.rs`. Verify WAL `PositionClosed` events can attribute to new strategy variants. | types/enums.rs + types/wal.rs | v20-FIX-10 |

**Gate**: All 15 items coded + unit tested; `cargo test` passes; `docker build` passes; crossbeam aggregate-on-overflow verified (no H/L data lost at 10,000 ticks/sec synthetic load); symbology mapper round-trip tested (IBKR→Polygon→IBKR); dynamic Kelly ramp produces £0 at 0 trades, £750 at 125 trades (£15k equity), £1,500 at 250 trades — **all valid entries after 250 trades**; `reqMarketDataType(3)` verified as first IBKR call in startup log; docker-compose.yml `stop_grace_period` and `POLARS_MAX_THREADS` verified present

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 22h | **Depends on**: Phase 8
*(+2h vs v19 for chrono-tz migration and IBKR subscription type migration)*

**Rationale**: The temporal spine. All subsequent phases depend on correct mode boundaries. **v20-FIX-6** replaces the BST approximation with `chrono-tz` — eliminates both the leap-year day-of-year error and the BST addition overflow bug simultaneously.

**Deliverables:**

- `clock.rs` REWRITTEN — BST logic replaced with chrono-tz **(v20-FIX-6)**:
  - `use chrono_tz::Europe::London;`
  - `fn now_london() -> DateTime<London>` — authoritative London local time
  - `fn from_utc_secs(s: u32) -> TradingMode` — uses chrono-tz conversion, no manual approximation
  - `TradingMode` enum: `{ModeA, ModeB, ModeBPlus, ModeC, Dark}`
  - `mode_b_plus_end_utc(date: NaiveDate) -> u32` using chrono-tz for DST-correct LSE close (output: UTC seconds — Rust converts, no Python IPC needed for mode boundary)
  - `NZX_OPEN_UTC_SECS: u32 = 23 * 3600` (MODE A open)
  - `NZX_CLOSE_UTC_SECS: u32 = 5 * 3600 + 45 * 60`
  - Cargo.toml: add `chrono-tz = "0.9"` dependency

- `subscription_manager.rs` (NEW, extends SC-02/SC-03/SC-11 skeleton):
  - Full `tokio::sync::Mutex`-guarded state machine **(v20-FIX-7)**
  - Deterministic: `cancel → wait for cancelMktData ACK → subscribe`
  - ACK received via AtomicUsize: `active_line_count` decrements when `cancelMktData` ACK arrives on callback thread
  - **Periodic reconciliation every 5 minutes (G-P6):** call `reqOpenOrders` to get IBKR current state; compare with `active_line_count`; if mismatch → log `LineBudgetDivergence` WAL event; reconcile to IBKR count
  - Timeout-based ACK: if no ACK within 2s → log timeout → proceed with subscribe + schedule reconciliation (G-40)
  - **IBKR bar vs tick migration:** Document that `ibkr_broker.rs` currently uses `subscribe_bars()` (5-sec OHLCV bars). Phase 11 MUST migrate to `reqMktData` tick-by-tick for the SubscriptionManager ACK protocol to function. The `active_line_count` tracking via `reqMktData` ACK events is incompatible with `subscribe_bars()`. Add migration as explicit Phase 11 sub-task (P1-14 resolution).
  - Proptest: 500 random subscribe/cancel sequences, `active_line_count <= 100` invariant holds
  - **Scanner Conservation Rule (GEM-A4):** `LineBudget::underlying_lines` increments only on position open; HotScanner/RotationScanner candidates NEVER trigger underlying subscription
  - Illiquid-ticker test: 10s silence must NOT be treated as cancel confirmation

- `mode_controller.rs` (NEW):
  - State machine driving mode transitions
  - Publishes `ModeChange { from, to, utc_ts }` events to engine
  - Event channel: **bounded capacity=16** (16 queued mode transitions is impossible in practice; prevents OOM on oscillation bug while not blocking on normal transitions)
  - Documents 2-5s scanning blind window during cancel→subscribe cycle (accepted behavior)
  - `ModeTransitionBlind` WAL event emitted during transition (not error)

- **NZX pre-subscribe at 22:55 UTC (G-57):** Mode controller fires `PreSubscribeNzx` event at 22:55 UTC during DARK. SubscriptionManager reserves NZX lines. When MODE A opens at 23:00 UTC, NZX subscriptions are already established.

**Acceptance Tests (AT-01 to AT-18):**
*(+2 new tests vs v19 for chrono-tz and bar→tick migration)*
- AT-01: ModeA boundary midnight wrap (23:59 UTC = ModeA, 00:01 UTC = ModeA)
- AT-02: ModeA → ModeB transition at 08:00 UTC
- AT-03: ModeB+ end DST: BST date = 15:30 UTC, GMT date = 16:30 UTC — verified via chrono-tz not approximation
- AT-04: DARK enforcement (21:00 UTC = DARK, 22:59 UTC = DARK)
- AT-05: NZX open at 23:00 UTC (MODE A start), not 21:00 UTC
- AT-06: Line budget proptest (1000 random sequences, invariant holds)
- AT-07: Illiquid ticker — 10s no-ticks — SubscriptionManager must NOT proceed with subscribe
- AT-08: Scanner Conservation Rule — 40 HotScanner tickers → `underlying_lines = 0`
- AT-09: Position open → underlying subscribed → `underlying_lines = 1`
- AT-10: Position close → underlying unsubscribed → `underlying_lines = 0`
- AT-11: Carry position → underlying stays subscribed during DARK mode
- AT-12: Mode transition cancel→subscribe → line count never exceeds 100 during transition
- AT-13: 5s blind window logged as `ModeTransitionBlind` event (not error)
- AT-14: ModeC → DARK: all scanner lines cancelled, carry lines retained
- AT-15: DARK → ModeA: scanner lines reestablished within 10s; NZX pre-subscribed (already established by 22:55 NZX event)
- AT-16: DST spring-forward: ModeB+ end shifts 1h, verified by chrono-tz `mode_b_plus_end_utc()`
- **AT-17: Leap year — Feb 29 2028 — BST approximation would fail, chrono-tz gives correct mode boundary**
- **AT-18: Periodic reconciliation: manually inject AtomicUsize mismatch → `LineBudgetDivergence` WAL event fires within 5 minutes**

**Gate**: 18 tests pass; chrono-tz DST flip verified at BST/GMT boundary (system time set to March 26 2026 01:59 UTC → 02:01 UTC); NZX pre-subscribe at 22:55 UTC verified; `active_line_count <= 100` proptest 1000 cases; bar→tick migration documented as sub-task with acceptance test

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 18h | **Depends on**: Phase 11
*(+3h vs v19 for snapshot timeout, board lot fallback, ISA boundary fix, tracking error check)*

**Rationale**: Routing logic determines which instrument we trade. **v19-FIX-5** resolves the data paradox. **v20** adds snapshot timeout (200ms, not blocking), ISA April 6 boundary, and HKEX board lot safety.

**Deliverables:**

- `smart_router.rs` (NEW):
  - ETP-first principle: ETP wins unless no ETP exists OR (direct_cost < etp_cost × 0.9 AND health passes)
  - **Snapshot with timeout (P1-1):** `reqMktData(conid, snapshot=True)` wrapped in `tokio::timeout(Duration::from_millis(200))`. On timeout → use ETP route (safe fallback). Snapshot queue: max 5 concurrent **(G-35)**. Consumes 0 streaming lines regardless.
  - Full cost model: FX drag + FTT + IBKR commission + stamp duty
  - Integer shares only: `filled_shares = (kelly_gbp / lot_price_gbp).floor() as u32`
  - **HKEX board lot ETP fallback (P1-12):** if `floor(kelly_gbp / lot_price) == 0` → route to LSE ETP overlay (e.g., HSBC → HSBA.L ETP equivalent)
  - **ETP 30-day tracking error check (G-68):** demote ETP preference if tracking error > 5% over last 30 days
  - **FTT market cap ±10% hysteresis (G-28):** FTT threshold crossed only if market_cap moves >10% beyond threshold boundary (prevents oscillation on boundary stocks)
  - `route(ticker, mode, portfolio_state) -> RouteResult`

- `isa_gate.rs` (NEW):
  - Hard-blocks: Taiwan (TWSE/GTSM), China (SSE/SZSE), India (BSE/NSE) — stored as `HashSet<&'static str>` for O(1) lookup
  - **ISA tax year boundary = April 6 (P1-11):** `fn isa_year_used(date: NaiveDate) -> f64` uses April 6 as fiscal year start. `isa_used_this_year_gbp` resets on April 6, not January 1.
  - ISA annual limit check (£20k per tax year)
  - ETP classification: Tier 1 priority routing

- FTT market-cap gate (integer bps storage **(P2-7)**):
  - `transaction_tax.toml` stores rates as integer bps: `france_ftt_bps = 30`, not `france_ftt_rate = 0.003`
  - `effective_rate_bps(market_cap_eur: f64, is_intraday: bool) -> u32`
  - France: 30 bps if market_cap > €1B AND NOT intraday, else 0
  - Italy: 10 bps if market_cap > €500M AND NOT intraday, else 0
  - Spain: 20 bps (no threshold, no intraday exemption)
  - **FTT no-carry flag (P1-9):** if FTT applicable (France/Italy market cap gate triggered) → tag `OrderIntent.ftt_jurisdiction = true` → `overnight_carry.rs` rejects carry for these positions

- `MINIMUM_ENTRY_GBP = 1500.0` wired into router pre-Kelly-submission check (suspended during ramp per SC-05)

- **Corporate Action Veto (v19-FIX-6 — Ouroboros-backed):** RiskGate reads `calibration/corp_action_blocklist.json` (atomic write, validated JSON); fires if `hours_until < 48`

**Acceptance Tests (AT-19 to AT-36):**
*(+6 tests vs v19 for new v20 items)*
- AT-19: ETP preferred over direct when costs equal
- AT-20: Direct preferred when 10% cheaper and health passes
- AT-21: ISA gate blocks TWSE ticker (Taiwan)
- AT-22: ISA gate blocks SSE ticker (China)
- AT-23: ISA gate blocks BSE ticker (India)
- AT-24: FTT France 30 bps for market_cap = €2B (integer bps storage verified)
- AT-25: FTT France 0 bps for market_cap = €500M (below threshold)
- AT-26: FTT France intraday: buy+sell same day → 0 bps
- AT-27: FTT Italy 10 bps for market_cap = €600M
- AT-28: FTT Italy 0 bps for market_cap = €400M
- AT-29: Min-lot enforcement: TSE lot=100, Kelly orders 50 shares → rejected; routed to ETP
- AT-30: ISA annual limit: £19,500 used + £600 order → rejected
- AT-31: Corporate action veto fires for ticker in blocklist with ex_date in 24h
- AT-32: Corporate action veto clears for ticker with ex_date > 48h away
- AT-33: Snapshot timeout: illiquid ticker → 200ms timeout → ETP route chosen (line count unchanged)
- AT-34: Snapshot queue: 6 concurrent snapshots → 5th queued, 6th queued, no more than 5 executing simultaneously
- **AT-35: ISA tax year boundary: April 5 → £19,500 used; April 6 (new year) → £0 used (reset)**
- **AT-36: ETP tracking error > 5% over 30 days → direct route preferred regardless of cost**

**Gate**: 18 tests pass; FTT bps integer arithmetic verified (no floating-point rounding); ISA April 6 boundary manual test; snapshot timeout verified (no streaming line consumed); board lot ETP fallback verified with HKEX lot=1000

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 22h | **Depends on**: Phase 12
*(+2h vs v19 for Gaussian-Gaussian Thompson Sampler and slot limit)*

**Rationale**: The signal layer. **v20-FIX-11** replaces Beta-Bernoulli (EV-blind) with Gaussian-Gaussian Thompson Sampler. **v20-FIX-10** ensures StrategyId enum variants exist for WAL attribution.

**Deliverables:**

- `hot_scanner.rs` (NEW):
  - Per-mode dispatch (ModeB/B+/C: US/European ticks; ModeA: Asian ticks)
  - **QuoteImbalance scoring** (renamed from OFI — academically accurate per Cont, Kukanov, Stoikov 2014): 5-second time-decay EWMA: `qi_ewma = α × qi_t + (1-α) × qi_ewma_prev` where `α = 1 - exp(-dt/5.0)`, `dt` = seconds since last tick
  - **CUSUM filter:** threshold h floor: `h = max(h_adaptive, 2.0 × bid_ask_spread_bps)`
  - Kalman price filter for trend estimation
  - Meta-label gate (threshold 0.55 — note: threshold should be ROC-optimized on first N=50 validated trades, but 0.55 is acceptable prior)

- `rotation_scanner.rs` (NEW):
  - **Gaussian-Gaussian Thompson Sampler (v20-FIX-11):** Normal-Normal conjugate model. Reward = continuous `pnl_pct` (not binary win/loss). Posterior update: `μ_post = (μ_0/σ_0² + Σ(r_i)/σ_noise²) / (1/σ_0² + n/σ_noise²)`. Sampling: draw `μ_sample ~ N(μ_post, σ_post²)`. Allocation: highest `μ_sample` gets next slot.
  - Prior: `μ_0 = 0.0`, `σ_0 = 0.05` (5% prior uncertainty), `σ_noise = 0.03` (3% observation noise)
  - **Hard slot limit (G-30):** max 40 HotScanner slots + 10 RotationScanner queue = 50 total scanner lines
  - Promotion threshold: score > 0.70 → move to HotScanner slot
  - Demotion threshold: score < 0.40 → return slot to queue
  - 60-second OHLCV snapshots for rotation candidates (via `reqHistoricalData` 1-min bars — clarified: snapshots don't consume streaming lines, resolves Scanner Conservation ambiguity)
  - **WAL attribution (v20-FIX-10):** `StrategyId::HotScanner` or `StrategyId::RotationScanner` set in `PositionOpened` WAL event. Thompson Sampler reads `PositionClosed.final_pnl` keyed by ticker + strategy_id.

- `universe_scanner.rs` (NEW):
  - US equity discovery via IBKR `reqContractDetails` (batched, ≤ 50 req/s limit)
  - ADV filter: minimum £50k daily average volume
  - RVOL calculation: real-time vs 20-day average
  - All scanner candidates respect 100-line budget from SubscriptionManager

**Acceptance Tests (AT-37 to AT-55):**
*(+2 new tests vs v19 for Gaussian-Gaussian TS and slot limit)*
- AT-37: QuoteImbalance 5s time-decay EWMA — fast market (200 ticks in 3s) gives higher intensity than slow market (200 ticks in 1h) for same tick imbalance
- AT-38: QuoteImbalance EWMA decay verified — value halves in 5 seconds after last tick
- AT-39: CUSUM h floor: spread=0.5%, h_adaptive=0.3% → h used = 0.5%
- AT-40: CUSUM h floor: spread=0.1%, h_adaptive=0.5% → h used = 0.5%
- AT-41: Kalman filter converges within 50 ticks of synthetic sine-wave price
- AT-42: **Gaussian-Gaussian TS: arm with 9×+1% + 1×(−20%) has lower μ_posterior than arm with 9×+1% + 1×(−1%)** — EV-aware allocation confirmed
- AT-43: **Gaussian-Gaussian TS: arm with higher mean PnL% gets more allocation over 500 simulated rounds**
- AT-44: RotationScanner promotion at score > 0.70
- AT-45: RotationScanner demotion at score < 0.40
- AT-46: ADV filter blocks ticker with £30k daily volume
- AT-47: ADV filter passes ticker with £80k daily volume
- AT-48: `reqContractDetails` batching ≤ 50 requests per second
- AT-49: HotScanner dispatches MODE A tickers only during ModeA
- AT-50: HotScanner dispatches US tickers only during ModeB+/ModeC
- AT-51: Meta-label gate blocks signal with probability 0.45
- AT-52: Meta-label gate passes signal with probability 0.60
- AT-53: Total scanner lines ≤ available from SubscriptionManager line budget
- AT-54: **WAL `PositionOpened` event contains `strategy_id = HotScanner` for HotScanner entries**
- AT-55: **Hard slot limit: 51st RotationScanner candidate rejected until a slot frees**

**Gate**: 19 tests pass; Gaussian-Gaussian TS arm-selection verified over 500 simulated rounds with negative-EV arm correctly de-prioritized; QuoteImbalance time-decay vs count-based comparison test; WAL attribution verified for both new StrategyId variants

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 22h | **Depends on**: Phase 13
*(+2h vs v19 for leverage-adjusted floor and TWAP US half-day handling)*

**Rationale**: Exit and execution engine. **v20** adds leverage-adjusted Chandelier floor (3x ETP spread is 3x wider), TWAP US half-day abort, and current-session-only volume for ADV gate.

**Deliverables:**

- `exit_engine.rs` EXTENDED — Infinite Chandelier with 8 adaptive multipliers:
  - M1 through M8 as specified in v19
  - **Leverage-adjusted Chandelier floor (P2-6):** `stop_distance = max(multiplier × ATR, 1.5 × bid_ask_spread × leverage_factor)`. `leverage_factor` from ETP metadata (3.0 for 3x ETPs, 1.0 for direct). Prevents immediate whipsaw on 3x instruments where spread is inherently wider.
  - Ratchet enforcer: `new_stop = max(old_stop, computed_stop)` — stop can ONLY increase
  - `ExitReason` enum: add `DustGuard` variant (Phase 15 uses it as a VetoReason; here add the ExitReason for when dust liquidation IS executed)

- `executioner_v2.rs` (NEW):
  - ADV execution gate: `order_size ≤ 1% of 5-min rolling volume from current session only` **(G-31 — pre-market volume excluded)**
  - U-shaped TWAP: slice sizes follow 60-day median volume curve
  - **TWAP US half-day abort (P2-5):** `early_close_detected()` — if exchange scheduled close is ≤ 30 min away → abort TWAP slices, submit remaining quantity as single limit order at mid
  - Partial fill dust check on FILLED portion (v19-FIX-1 retained): if `filled_gbp < 500.0` → Peg-to-Mid then market-sell **(SC-06 Peg-to-Mid amendment)**
  - Alpha half-life TWAP: slices spread across `alpha_halflife_secs` seconds

- `spread_veto.rs` (NEW):
  - U-shaped intraday spread tolerance: tight at open/close, wide at lunch
  - `spread_veto_threshold_bps(time_of_day: f32) -> f64`

**Acceptance Tests (AT-56 to AT-74):**
*(+2 new tests vs v19)*
- AT-56: All 8 multiplier outputs in range [0.5, 5.0] (bounded)
- AT-57: Ratchet proptest — 1000 random price sequences — stop never decreases
- AT-58: **Chandelier floor 3x ETP (leverage_factor=3.0): ATR=0.2%, spread=0.4% → stop_distance = max(0.2%, 1.5×0.4%×3.0) = max(0.2%, 1.8%) = 1.8%**
- AT-59: Chandelier floor direct equity (leverage_factor=1.0): ATR=0.5%, spread=0.1% → stop_distance = 0.5% (ATR dominates)
- AT-60: ADV gate blocks order >1% of 5-min **session** volume
- AT-61: ADV gate passes order ≤1% of 5-min session volume
- AT-62: ADV gate: pre-market volume excluded from 5-min rolling window
- AT-63: TWAP U-shape: open slice > midday slice (volume curve respected)
- AT-64: TWAP total slices fill 100% of order over alpha_halflife window
- AT-65: Partial fill FILLED portion £400 → Peg-to-Mid → fallback market-sell (dust liquidated)
- AT-66: Partial fill FILLED portion £600 → no dust action (above £500 threshold)
- AT-67: M3 regime tightening: BEAR_VOLATILE × multiplier < BULL_QUIET × multiplier
- AT-68: M8 correlation contagion: correlated asset -3% → multiplier tightened
- AT-69: Spread veto: 09:30 UTC threshold < 12:00 UTC threshold (tighter at open)
- AT-70: Spread veto: 15:30 UTC threshold < 12:00 UTC threshold (tighter at close)
- AT-71: Mega-runner carry eligibility at +102% unrealised gain
- AT-72: Mega-runner carry NOT triggered at +99%
- AT-73: Exit urgency score drives market vs passive order type selection
- **AT-74: TWAP US half-day abort: exchange close in 25 min → remaining quantity as single limit; no further TWAP slices**

**Gate**: 19 tests pass; ratchet proptest 1000 cases; leverage-adjusted floor verified at 3x ETP with wide spread; TWAP early-close abort verified; ADV gate confirmed uses current-session volume only

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 17h | **Depends on**: Phase 14
*(+2h vs v19 for VIX circuit breaker and dynamic Kelly ramp integration)*

**Rationale**: Expand from 22 to 31 vetoes. CVaR with Cornish-Fisher. **v20-FIX-12** adds VIX circuit breaker for DCC-GARCH cache invalidation during flash crashes. **v20-FIX-3** dynamic Kelly ramp fully integrated.

**Deliverables:**

- `risk_arbiter.rs` EXTENDED — 9 new veto checks added to existing 22:
  1. `ExchangeClosed` — outside exchange trading hours
  2. `AuctionAvoidance` — within 5 min of opening/closing auction
  3. `DarkModeActive` — fires FIRST during 21:00-23:00 UTC (pre-veto)
  4. `LunchBreakActive` — TSE/HKEX lunch suppression
  5. `DailyPriceLimitActive` — TSE ±20%, KRX ±30%
  6. `DustGuard` — position < £500 FILLED after partial fill
  7. `MinimumEntryGate` — order < £1,500 GBP equivalent (**suspended below 250 validated trades per SC-05**)
  8. `CVaRExceeded` — portfolio CVaR above floating limit
  9. `HMMLimitExceeded` — signal score below HMM regime floor

- `cvar_heat.rs` (NEW):
  - **Cornish-Fisher CVaR:** gated: must have N≥20 observations AND |skewness|<2 **(P2-2)**. Below gate → use normal CVaR.
  - CVaR limit floats: `heat_limit = base_heat × hmm_regime_factor × vix_factor`
  - Full 6-bucket table: 3 regimes × 2 VIX bands (BEAR_VOLATILE + VIX=40 → 1.08%)
  - `return_history: VecDeque<f64>` added to `portfolio.rs` for skewness/kurtosis computation **(MISSING-120 fix)**

- DCC-GARCH veto: computed async, cached with 5-min TTL, injected via `Arc<RwLock<GarchState>>`
  - **VIX circuit breaker (v20-FIX-12):** separate async task monitors VIX tick stream. If `|VIX_now - VIX_5min_ago| / VIX_5min_ago > 0.10` (10% spike) → immediately call `garch_state.invalidate_cache()`. Next CVaR computation uses conservative max-correlation matrix (all ρ=1.0) until next DCC-GARCH update cycle.

- **Dynamic Kelly ramp (v20-FIX-3):** `kelly_scale = max(0.1, min(1.0, validated_trades_count / 250.0))`. Trades counted from WAL `PositionClosed` events. Floor of 0.1× at 0 trades — the system can actually place orders from day 1 (min-entry gate suspended during ramp). At 250 trades: full Kelly (1.0×) enabled and min-entry gate reactivates at £1,500.
  - **RED tier min-entry suspension (G-52):** In RED tier recovery (within 5% of RED threshold exiting): temporarily suspend min-entry gate to allow position normalization.

**Acceptance Tests (AT-75 to AT-92):**
*(+2 new tests vs v19 for VIX circuit breaker and Kelly ramp)*
- AT-75: DarkModeActive veto fires before any other check during DARK window
- AT-76: ExchangeClosed veto fires for TSE at 10:00 UTC (mid-lunch)
- AT-77: LunchBreakActive veto fires for TSE at 02:45 UTC
- AT-78: LunchBreakActive veto fires for HKEX at 04:30 UTC
- AT-79: DailyPriceLimitActive fires for KRX at ±30% move
- AT-80: DailyPriceLimitActive fires for TSE at ±20% move
- AT-81: MinimumEntryGate blocks £1,400 order (at 250 validated trades)
- AT-82: MinimumEntryGate **passes £1,400 order at 0 validated trades** (gate suspended during ramp)
- AT-83: CVaR Cornish-Fisher diverges from normal at tail (S=−0.5, K=2.0 → CF > Normal by ≥10%)
- AT-84: **CVaR gate: N=15 observations → uses normal CVaR (not CF); N=25 observations + |S|<2 → uses CF**
- AT-85: CVaR heat limit table: BEAR_VOLATILE + VIX=40 → 1.08%
- AT-86: CVaR heat limit table: BULL_QUIET + VIX=15 → full 6% base
- AT-87: **VIX circuit breaker: VIX +11% in 1 min → DCC-GARCH cache invalidated; CVaR uses max-correlation matrix**
- AT-88: **VIX circuit breaker: VIX +9% in 1 min → DCC-GARCH cache NOT invalidated (below threshold)**
- AT-89: Dynamic Kelly ramp: 0 trades → kelly_scale=0.1 (floor; system CAN place orders, min-entry gate suspended)
- AT-90: Dynamic Kelly ramp: 125 trades → kelly_scale=0.5
- AT-91: Dynamic Kelly ramp: 250 trades → kelly_scale=1.0 (full Kelly; min-entry gate reactivates at £1,500)
- AT-92: DCC-GARCH result cached — verified NOT recomputed on each OrderIntent

**Gate**: 18 tests pass; 31 total vetoes confirmed; VIX circuit breaker end-to-end verified; dynamic Kelly ramp produces correct scaling at 0/125/250 trade milestones; Cornish-Fisher gating verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 22h | **Depends on**: Phase 15
*(+2h vs v19 for atomic blocklist write, sequential enforcement, Parquet cleanup)*

**Rationale**: Nightly intelligence pipeline. **v20** adds atomic blocklist write, explicit sequential step enforcement, Parquet cleanup, and better RSS management.

**Deliverables:**

- `ouroboros/` EXTENDED — 10-step pipeline (step 2 split into 2a+2b, renumbered cleanly):
  1. **Data fetch** — external bulk EOD via Polygon.io + IBKR active tickers; nightly cost basis clear + IBKR reqPositions resync (SC-10)
  2. **Corporate action blocklist** — Polygon.io `/v3/reference/dividends` + `/v3/reference/splits`; write `corp_action_blocklist.json.tmp`, validate with `json.loads()`, atomic `os.replace(tmp, final)` **(v19-FIX-6, P2-4)**
  3. **Universe discovery** — 5,000+ ticker screen using Polars LazyFrame, 500-ticker batches
  4. **Feature engineering** — Polars LazyFrame, Arrow zero-copy; write to `/dev/shm` during processing, then EBS for final output
  5. **Scoring** — ASER: momentum 30%, liquidity 20%, volatility 20%, regime 15%, recency 15%
  6. **Meta-label training** — Logistic Regression / LightGBM fallback
  7. **Chandelier calibration** — ATR, MAE/MFE profiling
  8. **Thompson Sampling update** — Gaussian-Gaussian posteriors from WAL outcomes, keyed by StrategyId
  9. **DCC-GARCH update** — cross-asset correlation matrix; write `calibration/asia_cross_tz.json` with `updated_at` timestamp
  10. **PDF generation + artifact write + Telegram 🟡 ALIVE** — daily summary report only; intraday liveness is the engine-side Redis heartbeat (written every 30 min by Rust, independent of Ouroboros)

- **Sequential step enforcement (G-51, P1-13):** each step executed via `run_step(n, fn)` wrapper that checks `ouroboros_step_{n-1}_ts` before starting. No async parallelism between steps. Steps are CPU-bound; parallelism causes OOM on 4GB EC2.

- **Polars mandate (GEM-A1):** `import pandas` banned. ALL processing via `polars.LazyFrame`. `POLARS_MAX_THREADS=2` from docker-compose.yml env. 500-ticker batch processing.

- **Polars LazyFrame validation (G-37):** call `.explain()` on each LazyFrame plan at Ouroboros module import time (not execution time). If Polars raises `InvalidOperationError` → Telegram alert before any processing begins.

- **RSS pre-allocation check (G-10):** use `resource.getrlimit(resource.RLIMIT_AS)` to check available virtual memory before starting step 3 (feature engineering). If available < 1GB → abort with Telegram 🔄 SYSTEM SHIFT alert.

- **Parquet cleanup (P2-15, G-50):** all Parquet files written to `/tmp` (or `/dev/shm` for performance). `cleanup_step_artifacts(step_n)` called at end of each step: `glob.glob('/tmp/ouroboros_step_*.parquet') | rm`.

- Step checkpointing: `ouroboros_step_N_ts` Redis SETEX with 24h TTL after each of the 10 steps; resume from last successful step on restart; `pipeline_complete` Redis flag set only after step 10 finishes

- **Ouroboros failure escalation (GEM-A5):** 22:55 UTC watchdog checks `pipeline_complete` Redis flag. If not set → `DrawdownTier::Yellow` (Kelly sizing halved: `kelly_scale × 0.5`, no new entries opened, existing positions managed normally) → Telegram 🔄

**Acceptance Tests (AT-93 to AT-110):**
*(+2 new tests vs v19)*
- AT-93: Polars LazyFrame processes 500-ticker batch without OOM (RSS ≤ 3.5GB)
- AT-94: Memory cleared between batches (RSS drops after `del df; gc.collect()`)
- AT-95: Pipeline checkpoint resume from step 6 (meta-label training) after simulated crash at end of step 5 — steps 1-5 skipped on restart
- AT-96: WAL calibration read: avg_win/avg_loss correctly parsed from last 100 trades
- AT-97: Ouroboros failure at 22:55 UTC → DrawdownTier::Yellow set in engine
- AT-98: Yellow tier: new entry rejected, existing position managed normally
- AT-99: Yellow tier cleared on manual RESUME Telegram command
- AT-100: Telegram SYSTEM SHIFT fires on Yellow escalation
- AT-101: ADV 1% cap governs sizing at £50k AUM (no log taper applied)
- AT-102: External bulk EOD fetch returns 5,000+ tickers without IBKR Error 162
- AT-103: IBKR `reqHistoricalData` used for ≤ 100 active tickers only
- AT-104: DCC-GARCH correlation matrix positive semi-definite
- AT-105: Thompson Sampling posteriors updated from WAL trade outcomes (Gaussian-Gaussian)
- AT-106: Shadow book divergence > £50 → WAL event logged (threshold raised from £5 per P2-1)
- AT-107: Corporate action blocklist written atomically (tmp → rename, no partial read possible)
- **AT-108: Sequential step enforcement: step 4 (feature engineering) blocked if step 3 (universe discovery) checkpoint missing**
- **AT-109: Parquet cleanup: after step 4 completes, no `/tmp/ouroboros_step_4_*.parquet` files remain**
- **AT-110: LazyFrame `.explain()` validation fires Telegram alert on syntax error (not runtime crash)**

**Gate**: 18 tests pass; Polars run verified ≤ 3.5GB RSS on 1000-ticker test; checkpoint resume verified; Yellow escalation end-to-end verified; atomic blocklist write verified (simulate mid-write kill, final file is always valid JSON)

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 14h | **Depends on**: Phase 16
*(+2h vs v19 for engine-side heartbeat, polling thread retry, shadow book threshold, asyncio isolation)*

**Rationale**: Operational visibility. **v20-FIX-9** moves heartbeat to engine Rust loop — decouples from Ouroboros schedule, provides 30-min intraday heartbeat.

**Deliverables:**

- **Engine-side heartbeat (v20-FIX-9):** `engine.rs` main loop writes `aegis_heartbeat_ts` to Redis via `SETEX aegis_heartbeat_ts {unix_ts} 7200` (2h TTL) every 30 minutes. Separate from Telegram 🟡 ALIVE message. Watchdog reads Redis; if `aegis_heartbeat_ts` expired → fires Telegram alert from watchdog account.

- `telegram_reporter.py` (NEW): `AegisTelegramReporter` async class
  - 4 alert types (TARGET ACQUIRED, CHANDELIER SEVERED, MEGA-RUNNER CARRY, SYSTEM SHIFT)
  - Rate limiter: max 1 msg/5s (HALT bypasses)
  - Message truncated at 4000 chars at last complete JSON field boundary (not mid-string) **(G-23)**

- **Long-polling architecture (v19-FIX-4 + v20):**
  - `python-telegram-bot` v20+ (async API, `Application.run_polling()`)
  - Dedicated Python thread: `threading.Thread(target=run_polling, daemon=False)` — **NOT daemon=True** (daemon threads die instantly on SIGTERM, dropping in-flight HALT commands)
  - **Infinite retry loop (P1-2):** `while True: try: app.run_polling(); except: backoff(); continue` — never exits on network exception
  - **asyncio isolation (G-9):** polling thread runs its own `asyncio.new_event_loop()` — no GIL contention with main Python bridge event loop
  - HALT command: `mpsc::Sender<HaltCommand>` → engine reads within 1 Tokio event loop tick

- **Heartbeat (redesigned — two distinct signals, do not confuse them):**
  - **Intraday liveness:** `engine.rs` writes `aegis_heartbeat_ts` to Redis every 30 min (Rust, continuous). Watchdog reads Redis; expired key → Telegram alert. Completely independent of Ouroboros.
  - **Daily report:** Telegram 🟡 ALIVE fires from Ouroboros step 10 once per night (equity, position count, pipeline status). This is an informational summary — NOT the heartbeat. Missing a step-10 Telegram is expected when Ouroboros escalates to Yellow; the Redis heartbeat continues unaffected.

- **Shadow book threshold raised (P2-1, G-64):** `ShadowBookDivergence` WAL event fires if divergence > max(£50, 0.5% of position value). Prevents false alert on every normal commission rounding.

- `pdf_generator.py`: PyMuPDF `fitz.Story`. Explicitly `pip install pymupdf` (not `fitz`) in Docker. Test with `import fitz`.
  - PDF1 + PDF2 as specified in v19
  - **No tmpfs limit restriction in Docker** (remove `--tmpfs /tmp:size=64m` if present; 76-page PDF can exceed 64MB)

- **Redundant watchdog (G-54):** EC2 cron + personal phone HTTP check. Both must be set up.

**Acceptance Tests (AT-111 to AT-127):**
*(+2 new tests vs v19)*
- AT-111: TARGET ACQUIRED fires on position open
- AT-112: CHANDELIER SEVERED fires on exit via trailing stop
- AT-113: MEGA-RUNNER CARRY fires at +102% unrealised gain
- AT-114: SYSTEM SHIFT fires on HMM regime change
- AT-115: HALT command via Telegram → engine receives within 100ms
- AT-116: HALT bypasses 1msg/5s rate limiter
- AT-117: Non-HALT messages respect 1msg/5s rate limit
- AT-118: **Engine-side Redis heartbeat: `aegis_heartbeat_ts` key present in Redis every 30 min during active session (verified over 2h)**
- AT-119: **Missed heartbeat × 2 (60 min gap) → watchdog fires Telegram alert from watchdog account**
- AT-120: PDF1 output is valid PDF bytes (fitz can open it)
- AT-121: PDF2 output is valid PDF bytes
- AT-122: Message > 4000 chars truncated at word/JSON boundary (not mid-field)
- AT-123: Shadow book divergence £60 → WAL event logged
- AT-124: Shadow book divergence £40 → no WAL event (below new £50 threshold)
- AT-125: Telegram 429 (rate limit) → exponential backoff, message delivered within 30s
- **AT-126: Polling thread survival: kill network interface 10s → restore → polling thread reconnects (infinite retry loop)**
- **AT-127: SIGTERM during active HALT processing: polling thread is NOT daemon, stays alive during 30s SIGTERM wait**

**Gate**: 17 tests pass; manually verify Telegram message arrives within 100ms for HALT; both PDFs open in viewer; engine heartbeat verified in Redis over 2h; polling thread survives network kill-restore; **verify daemon=False on polling thread**

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 20h | **Depends on**: Phase 17
*(+2h vs v19 for FTT integer bps, IDEALPRO routing, Decimal crate, VPIN NaN guard)*

**Rationale**: 15 European exchanges. **v20** adds FTT integer bps storage, IDEALPRO enforcement for FX pairs, VPIN NaN guard for newly-listed equities.

**Deliverables:**

- `currency.rs` (NEW): `FxRateTable` — 6 currencies (EUR/CHF/SEK/NOK/DKK/PLN)
  - **IDEALPRO routing enforced (MISSING-116):** FX pair subscriptions (`EUR.GBP`, `CHF.GBP`, etc.) routed via `IDEALPRO` exchange in `reqMktData`. Default SMART routing returns Error 200 for FX pairs. `FxRateTable::refresh()` explicitly sets `contract.exchange = "IDEALPRO"`.
  - Stale-rate detection (>4h → halt new positions in that currency)
  - FX drag included in all Kelly sizing

- `exchange_profile.rs` (NEW): 15 European exchange profiles
  - All 15 exchanges as specified in v19
  - XETRA closing auction cutoff T-5 = 15:25 UTC

- `transaction_tax.rs` (NEW):
  - **Integer bps storage (P2-7):** all rates in `transaction_tax.toml` stored as `u32` bps. `0.003` replaced by `30_u32` bps. No floating-point multiplication for FTT computation.
  - **`Decimal` crate for tick-size rounding (G-24, P2-12):** Euronext orders use `rust_decimal::Decimal` for lot/price arithmetic. No `f64` tick-size rounding errors.
  - Per-exchange stamp duty map (G-55): separate from FTT, per-exchange `HashMap<&'static str, u32>` with bps values.
  - France: 30 bps if >€1B AND not intraday; Italy: 10 bps if >€500M AND not intraday; Spain: 20 bps; etc.
  - **FTT no-carry flag (P1-9):** positions entered in FTT jurisdiction → `ftt_jurisdiction = true` in OrderIntent → carry eligibility blocked

- `sub_universe_allocator.rs` (NEW):
  - Thompson Sampling for MODE B/B+ splits
  - **Adaptive VPIN (GEM A7):** `vpin_bucket_threshold = max(5-day_median_ADV × 0.002, min_threshold)`
  - **VPIN NaN guard (P2-8, MISSING-124):** if `ticker_days_of_data < 5` → disable VPIN, use spread-only liquidity check

**Acceptance Tests (AT-128 to AT-150):**
*(+2 new tests vs v19)*
- AT-128: EUR FX drag included in Kelly sizing for Euronext positions
- AT-129: FX stale rate (>4h): no new positions in that currency
- AT-130: FTT France: €2B market cap → 30 bps (integer) applied
- AT-131: FTT France: €500M → 0 bps
- AT-132: FTT France intraday: buy+sell same day → 0 bps
- AT-133: FTT Italy: €600M → 10 bps; €400M → 0 bps
- AT-134: XETRA EOD flatten at 15:25 UTC (not 16:30 UTC)
- AT-135: Dual-listing ISIN dedup: ASML on Euronext + XETRA → only highest-ADV venue subscribed
- AT-136: Adaptive VPIN bucket: low-ADV ticker → smaller bucket
- AT-137: **VPIN NaN guard: newly-listed ticker (3 days data) → VPIN disabled, spread-only liquidity**
- AT-138: **IDEALPRO routing: EUR.GBP `reqMktData` uses `exchange=IDEALPRO`, not SMART**
- AT-139: Per-exchange EOD flatten: Borsa Italiana at 15:30 UTC (not 16:30 UTC)
- AT-140: Tick-size rounding: Euronext order for €51.23 → €51.25 (0.05 tick) using Decimal crate
- AT-141: SubUniverseAllocator min_fraction: only 3/15 exchanges open → reduced min_fraction
- AT-142: Spain FTT 20 bps applied regardless of market cap
- AT-143: Ireland stamp duty 100 bps applied
- AT-144: Germany (XETRA): 0 FTT, 0 stamp duty
- AT-145: FTT no-carry flag: French stock with 30 bps FTT → `ftt_jurisdiction = true`, carry rejected
- AT-146: FTT integer bps: `30 bps × 10,000 = 300,000` (no floating-point rounding error)
- AT-147: FTT market cap hysteresis: cap oscillates ±5% around €1B threshold → FTT does NOT oscillate (hysteresis holds)
- **AT-148: ETP 30-day tracking error: error > 5% → direct route preferred**
- **AT-149: FX pair Error 200 prevention: SMART routing rejected; IDEALPRO returns valid quote**
- AT-150: Thompson Sampling: ETP vs direct allocation adapts to win rates

**Gate**: 23 tests pass; 5 paper trading days with European tickers active; IDEALPRO routing verified (IBKR returns quote, not Error 200); FTT integer bps arithmetic verified; VPIN NaN guard verified on newly-listed ticker

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 20h | **Depends on**: Phase 18
*(+2h vs v19 for IBKR reconnect max_attempts extension, ASX DST dynamic, KRX VI confirmation)*

**Rationale**: MODE A clock, 6 Asian exchanges. **v20** extends IBKR reconnect attempts to cover the full 3-min GW restart window, adds dynamic ASX DST detection, and improves KRX VI confirmation.

**Deliverables:**

- `asian_exchange.rs` (NEW): 6 exchange profiles as specified in v19 plus:
  - **ASX DST dynamic detection (P2-9):** Ouroboros init: `from zoneinfo import ZoneInfo; from datetime import datetime; offset = ZoneInfo("Australia/Sydney").utcoffset(datetime.utcnow())`. If `offset.seconds == 39600` (UTC+11, AEDT) → `ASX_OPEN_UTC = 23 * 3600`. If `offset.seconds == 36000` (UTC+10, AEST) → `ASX_OPEN_UTC = 0 * 3600`. Written to `calibration/exchange_times.json` for Rust engine to read at MODE A open.
  - **KRX VI confirmation (G-15):** after 120s VI halt expires, call `reqContractDetails` to verify `TRADING_HOURS` field shows continuous trading resumed before lifting `VetoReason::VolatilityInterruptionActive`

- `clock.rs` EXTENDED (Phase 19 extension):
  - 04:45 UTC IBKR reconnect handler: suspend tick delivery → reconnect → resume
  - **IBKR reconnect max_attempts = 20 (P2-14):** exponential backoff with jitter; total max backoff ~5 minutes (covers 3-min GW restart window). V19 had 5 attempts (~31s total — not enough).

- ISA triple-gate hard-coded: `BLOCKED_EXCHANGES = ["TWSE", "GTSM", "SSE", "SZSE", "BSE", "NSE"]`

**Acceptance Tests (AT-151 to AT-171):**
*(+2 new tests vs v19)*
- AT-151 through AT-166: (same as v19 AT-131 to AT-146, renumbered)
- AT-167: NZX subscriptions begin at 22:55 UTC (pre-subscribed during DARK, not at MODE A open)
- AT-168: IBKR 04:45 UTC disconnect: reconnect within 5 minutes (20 attempts, covers GW restart)
- AT-169: IBKR 04:45 UTC: all 5 attempts in v19 exhausted at ~31s → engine would give up. v20: attempt 20 at ~300s → engine reconnects successfully.
- AT-170: HKD concentration: £10k HKD position counted as £8k USD exposure
- **AT-171: ASX DST: AEDT simulation → ASX_OPEN_UTC = 23×3600 read from exchange_times.json; AEST simulation → ASX_OPEN_UTC = 0×3600**

**Gate**: 21 tests pass; ASX DST detected dynamically via ZoneInfo (verified with both AEDT and AEST simulation); KRX VI confirmation via reqContractDetails; IBKR reconnect survives 3-minute simulated GW restart

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 22h | **Depends on**: Phase 19
*(+2h vs v19 for account-level reqPnL, holiday-aware day counter, allocator fix)*

**Rationale**: Carry positions crossing timezone sessions. **v20-FIX-5** replaces reqPnLSingle with account-level reqPnL. **v20-FIX-14** fixes the carry allocator formula.

**Deliverables:**

- `overnight_carry.rs` (NEW):
  - Full state machine: `LIVE → CARRIED → MONITORED → REACTIVATED → CLOSED`
  - HALTED branch: `MONITORED → HALTED (circuit breaker) → MONITORED (resolved)`
  - `MAX_CARRY_POSITIONS: usize = 6`
  - Mega-runner threshold: +102% unrealised gain
  - Stop freeze: Chandelier stop frozen in CARRIED + MONITORED states
  - **FTT no-carry enforcement (P1-9):** positions with `ftt_jurisdiction = true` rejected by `try_carry()`

- **Account-level reqPnL (v20-FIX-5):** Single `ibkr.req_pnl(pnl_req_id, account, "")` subscription. Receives real-time PnL updates for ALL positions in one stream. `CarryMonitor::on_account_pnl_update()` dispatches to per-position handlers by `conid`. Consumes 0 market data lines. `cancel_pnl(pnl_req_id)` called on session end (NOT per-position).

- **Holiday carry (Gemini G-04):** On MODE C → DARK, check `reqTradingHours` per carry exchange. Holiday → MONITORED. Recheck each DARK cycle.

- **Dynamic carry allocator (v20-FIX-14):** `scanner_available = 100 - (current_carry_count × 2) - active_scanner_lines`. Updated atomically whenever carry_count or scanner_lines changes. Hard assert: `scanner_available >= 0`.

- HALTED state rules:
  - Max HALTED duration: 2 **trading days** (holiday-aware: `reqTradingHours` used to count trading days, not calendar days) **(MISSING-117 fix)**
  - **Day 3 market order submitted at exchange OPEN TIME (not UTC midnight) (INFRA-177 fix)**
  - No orders submitted during HALT; reqPnL monitoring continues; stop frozen

- **IBKR margin check in MONITORED holiday state (G-16):** if IBKR increases margin requirement for carry position during holiday period, `on_account_pnl_update()` checks margin available; if margin_available < 110% of initial margin → submit exit order

- Telegram: `🚨 HALT: [TICKER] exchange circuit breaker active` on HALTED transition

**Acceptance Tests (AT-172 to AT-195):**
*(+4 new tests vs v19 for account reqPnL, trading-day counter, allocator fix)*
- AT-172 through AT-185: (carry state machine as per v19 AT-150 to AT-163, renumbered)
- AT-186: Holiday carry: HKEX holiday → MONITORED, recheck next DARK
- AT-187: Holiday resolved: exchange reopens → REACTIVATED
- AT-188: **Account-level reqPnL: 1 subscription for 6 carry positions → no IBKR Error 10197**
- AT-189: **Account-level reqPnL: position close → single `cancel_pnl` call (not 6 separate cancel calls)**
- AT-190: **HALTED day counter: Friday HALT + weekend → Day 1 = Monday, Day 2 = Tuesday. Day 3 market order submitted at exchange open Wednesday (not UTC midnight Sunday)**
- AT-191: **Carry allocator: 6 carry positions → scanner_available = 100 − 12 − active_scanner; assert ≥ 0**
- AT-192: **Carry allocator: 0 carry positions → scanner_available = 100 − 0 − active_scanner (dynamic)**
- AT-193: FTT no-carry: French stock with FTT flag → try_carry() returns CarryError::FttJurisdiction
- AT-194: IBKR margin check during holiday MONITORED: margin_available < 110% → exit order submitted
- AT-195: reqPnL heartbeat timeout: 60s no update → assume carry monitoring stale → escalate

**Gate**: 24 tests pass; carry state machine proptest (100 random event sequences, invariants hold); account-level reqPnL verified over 2h paper session (no IBKR error); HALTED day counter holiday-aware test; allocator formula verified with 6 carry positions

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13h | **Depends on**: Phase 20
*(+1h vs v19 for artifact timestamp validation)*

**Rationale**: Dynamic DCC-GARCH cross-session weights. **v20** adds artifact timestamp validation to detect the case where step 9 (DCC-GARCH computation) completes but step 10 (artifact write) fails — leaving a stale JSON on disk that the watchdog `pipeline_complete` flag would not catch.

**Deliverables:**

- `cross_timezone.py` (NEW): DCC-GARCH weights (nightly, Ouroboros step 9)
  - Stored in `calibration/asia_cross_tz.json` with `{"weights": {...}, "updated_at": unix_ts, "step9_complete": true}`
  - **Artifact validation (RISK-64 fix):** at MODE A open, check `updated_at` within last 26 hours AND `step9_complete == true`. If stale OR flag missing → use previous day's weights AND fire Telegram SYSTEM SHIFT "DCC-GARCH weights stale". Watchdog `pipeline_complete` flag is set by step 10 — if step 9 ran but step 10 write failed, `pipeline_complete` may still be absent, but this check catches step 9 failing silently even when step 10 succeeds.

- `asia_universe.py` (NEW): ISA eligibility + ETP/GDR overlay as per v19

- **ES futures via IBKR tick (G-58, RISK-21):** real-time S&P 500 sentiment uses IBKR ES futures tick stream (not Ouroboros cache). Latency: milliseconds. `cross_timezone.py` only handles overnight DCC-GARCH weights. ES tick is streamed via the normal SubscriptionManager (consumes 1 IBKR line during ModeC+).

**Acceptance Tests (AT-196 to AT-207):**
*(+1 new test vs v19 for artifact validation)*
- AT-196 through AT-206: (as per v19 AT-170 to AT-179, renumbered)
- **AT-207: asia_cross_tz.json stale (>26h old): MODE A open detects stale, uses previous day's weights, fires Telegram SYSTEM SHIFT. Engine does NOT halt — degraded mode.**

**Gate**: 12 tests pass; 5 paper trading days with Asia session active; artifact timestamp validation tested with manually-outdated JSON; DCC-GARCH weights verified as adaptive

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 28h | **Depends on**: Phase 21
*(+3h vs v19 for WAL compaction open-position exclusion, NTP check, ArcSwap config, Prometheus security)*

**Rationale**: Pre-Crucible production hardening. **v20-FIX-4** adds open-position exclusion to WAL compaction. NTP check added to startup gate. ArcSwap for SIGHUP safety.

**Deliverables:**

- **SIGTERM end-to-end drill**: container kill → positions flatten → WAL write → restart → positions recovered from WAL

- **WAL compaction — open position exclusion (v20-FIX-4):**
  - Before deleting any file in the 30-day rolling window: `compaction_job.exclude_open_positions(portfolio.positions())`. For each open position `ticker_id`: scan ALL WAL files (including those >30 days) for the earliest event with `ticker_id == open_position.ticker_id`. This event must NEVER be deleted. Write `compaction_manifest.json` listing preserved event IDs.
  - `compaction_metrics.json` written; dead-letter count alerted

- **NTP sync check (G-46):** `startup_gate.py` pre-flight check H-09: call `ntplib.client()` and verify system time offset < 500ms vs NTP. If drift > 500ms → halt startup with alert.

- **ArcSwap config reload (G-60):** Replace `Arc<RwLock<Config>>` with `ArcSwap<Config>` (or `arc-swap` crate). SIGHUP handler: validate new config → if valid, `config_swap.store(Arc::new(new_config))`. Readers see new config on next load without blocking.

- **Chaos suite (Gemini + v20):**
  - Python bridge crash → dry-run mode (confirmed: silent None return is de-facto dry-run; add explicit `PythonBridgeFailed` WAL event)
  - IBKR disconnect at 04:45 UTC → reconnect within 5 min (20 attempts); carry positions reconciled
  - Redis OOM-kill → WAL rebuild before any trading resumes; positions intact
  - WAL disk-full → halt gracefully; Telegram alert; does NOT trade without WAL
  - **Delayed data detection (G-65):** monitor `reqMarketDataType` response code on each subscription. If response = 2 (frozen/delayed) when requesting type 3 (delayed permissioned) → log `DelayedDataWarning`; halt signal generation; Telegram alert

- **Rate limiter audit**: all IBKR calls within 50 req/s

- **`reqMarketDataType(3)` audit**: verify call exists and fires first in startup logs

- **Prometheus `/metrics` on localhost only (security):** bind to `127.0.0.1:9090`, NOT `0.0.0.0`. External Prometheus scraper tunnels via SSH. Prevents equity/drawdown exposure on public EC2 interface.

- **ArcSwap config hot-reload (G-60):** SIGHUP → safe reload, no blocking under load

**Acceptance Tests (AT-208 to AT-226):**
*(+2 new tests vs v19 for open-position WAL exclusion and NTP check)*
- AT-208: SIGTERM drill — container killed mid-position → WAL written → restart → position recovered
- AT-209: **WAL compaction: 31-day carry position entry event NOT deleted despite >30-day rolling window**
- AT-210: **WAL compaction: events for CLOSED positions > 30 days ARE deleted**
- AT-211: Rate limiter: 100 req/s synthetic → throttled to 50 req/s
- AT-212: Python bridge crash → `PythonBridgeFailed` WAL event → dry-run; Chandelier exits still execute
- AT-213: Dry-run mode: existing positions managed; no new signals generated
- AT-214: IBKR disconnect at 04:45 UTC → reconnect within 300s (5 min, 20 attempts)
- AT-215: Redis OOM-kill → WAL rebuild before trading resumes; positions verified vs pre-kill state
- AT-216: WAL disk-full → `WalDiskFull` event → halt trading → Telegram alert
- AT-217: Polars Ouroboros: 5,000-ticker run ≤ 3.5GB RSS peak
- AT-218: 24h paper run RSS growth ≤ 5%
- AT-219: `reqMarketDataType(3)` verified as FIRST call in startup logs
- AT-220: SIGHUP config reload: ArcSwap swap completes in < 1ms; no position impact; no blocking reads
- AT-221: `/metrics` bound to 127.0.0.1 only (verify `netstat -an | grep 9090` → no 0.0.0.0 binding)
- AT-222: Dead-letter WAL > 0 → Telegram alert fires
- AT-223: Heartbeat maintained during chaos tests (no false watchdog alerts during reconnect)
- **AT-224: NTP check: system time offset > 500ms → startup gate FAILS with alert**
- **AT-225: Delayed data detection: `reqMarketDataType` returns code 2 → `DelayedDataWarning` logged; signal generation halted**
- **AT-226: WAL compaction manifest: `compaction_manifest.json` lists all preserved event IDs for open positions**

**Gate**: 19 tests pass; 48h continuous paper run without HALT; WAL compacted with open positions excluded (verified manually); NTP check passes on clean EC2; ArcSwap reload timing < 1ms; all chaos scenarios recovered

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22

**Rationale**: Formal proof of correctness before any live capital. **v20** corrects the Romano-Wolf Bonferroni overcorrection.

**Deliverables (7 test suites):**

1. **Suite 1 — Trade Gate**
   - WR ≥ 40% on last 100 paper trades
   - **Single-hypothesis t-test (N=1) (IMPROVEMENT-92 fix):** `t-stat = (mean_pnl - 0) / (std_pnl / sqrt(100))`. t-stat ≥ 2.0 (two-tailed, df=99). Bootstrap resampling: 1,000 iterations, 95th percentile CI on WR and Sharpe. (v19 used N=20 Bonferroni correction — wrong for single-strategy test)
   - Sharpe (cost-adjusted) > 0
   - Zero HALT events triggered by system errors
   - Max drawdown < 8%

2. **Suite 2 — SIGTERM Flatten Drill**
   - Kill container mid-position (3 open positions)
   - Flat on restart, WAL consistent, no orphans
   - Repeat 5 times

3. **Suite 3 — 48h Paper Shadow Run**
   - Shadow book vs broker: max divergence < £50 at any point (raised from £5 per P2-1)
   - All mode transitions logged with latency < 50ms

4. **Suite 4 — Chaos Engineering**
   - Python bridge crash, IBKR kill, Redis kill — all recovered in sequence

5. **Suite 5 — ISA Compliance Audit**
   - 200 synthetic order intents; 0 short orders, 0 Taiwan/China/India; 0 exceeding £20k
   - WAL `CorporateActionVeto` event type fires for synthetic blocklist ticker (MISSING-134 fix: add this WAL event type)
   - `isa_compliance_audit.json` generated

6. **Suite 6 — Line Budget Stress Test**
   - proptest 1,000 sequences; `active_line_count <= 100` invariant NEVER violated
   - Scanner Conservation Rule holds; HotScanner/RotationScanner → 0 underlying lines

7. **Suite 7 — Full Mode Cycle**
   - 24h paper run: ModeA → DARK → ModeB → ModeB+ → ModeC → DARK
   - DST boundary handled (chrono-tz verified)
   - NZX pre-subscribed at 22:55 UTC
   - Ouroboros completes all 9 steps within DARK

**Gate**: All 7 suites pass with written sign-off. 100 validated paper trades. No P0 bugs open. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE ✓** | 147+ (all passing) |
| **8** | Pre-Conditions + P0 (SC-01→SC-15 incl. v20 fixes) | **40h** | **NEXT** | Unit tests per SC item |
| **11** | 5-Mode Clock + SubscriptionManager (chrono-tz) | **22h** | NOT STARTED | AT-01→18 |
| **12** | Smart Router + ISA Gate + snapshot timeout + board lot fix | **18h** | NOT STARTED | AT-19→36 |
| **13** | HotScanner + RotationScanner (Gaussian-Gaussian TS) | **22h** | NOT STARTED | AT-37→55 |
| **14** | Infinite Chandelier (leverage floor) + Executioner V2 | **22h** | NOT STARTED | AT-56→74 |
| **15** | RiskGate 31 Vetoes + CVaR + VIX circuit breaker | **17h** | NOT STARTED | AT-75→92 |
| **16** | Ouroboros + Polars + sequential steps + atomic blocklist | **22h** | NOT STARTED | AT-93→110 |
| **17** | Telemetry (engine heartbeat + polling retry + shadow £50) | **14h** | NOT STARTED | AT-111→127 |
| **18** | European Equities + FTT bps + IDEALPRO + VPIN NaN guard | **20h** | NOT STARTED | AT-128→150 (+5 paper days) |
| **19** | Asia-Pac MODE A + ASX DST dynamic + reconnect max 20 | **20h** | NOT STARTED | AT-151→171 |
| **20** | Carry State Machine (account reqPnL + allocator fix) | **22h** | NOT STARTED | AT-172→195 |
| **21** | Cross-Timezone Intelligence (artifact validation) | **13h** | NOT STARTED | AT-196→207 (+5 paper days) |
| **22** | Institutional Hardening (WAL compaction + ArcSwap + NTP) | **28h** | NOT STARTED | AT-208→226 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **322h** | | **AT-01→AT-226 = 226 tests** |

*(+10h vs v19: SC-13/14/15 additions, amendment hours across phases. +32 tests vs v19: 194→226)*

**At 20h/week**: ~16.1 weeks to live capital
**At 40h/week**: ~8.1 weeks to live capital

---

### Drawdown Tier Reference

| Tier | Kelly Sizing | New Entries | Existing Positions | Trigger |
|------|-------------|-------------|-------------------|---------|
| NORMAL | `kelly_scale × 100%` | ✓ Allowed | Managed normally | Default |
| **YELLOW** | `kelly_scale × 50%` | ✗ Blocked | Managed normally (exits still fire) | Ouroboros failure; drawdown −3% |
| **ORANGE** | 0% | ✗ Blocked | Close all positions at market | Drawdown −5% |
| **RED** | 0% | ✗ Blocked | Full halt (no exits, no orders) | Drawdown −8%; manual RESUME only |

*`kelly_scale = max(0.1, min(1.0, validated_trades / 250))` — ramps from 0.1× at 0 trades to 1.0× at 250 trades. Yellow halves whatever the ramp currently produces. ORANGE and RED override everything.*

---

### New Files Created in Phases 8-23

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — tokio::sync::Mutex
├── mode_controller.rs         (Phase 11) — chrono-tz boundaries
├── smart_router.rs            (Phase 12) — snapshot + timeout
├── isa_gate.rs                (Phase 12) — April 6 boundary
├── hot_scanner.rs             (Phase 13) — QuoteImbalance EWMA
├── rotation_scanner.rs        (Phase 13) — Gaussian-Gaussian TS
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — leverage-adjusted floor
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — VIX circuit breaker
├── overnight_carry.rs         (Phase 20) — account-level reqPnL
├── currency.rs                (Phase 18) — IDEALPRO routing
├── exchange_profile.rs        (Phase 18)
├── transaction_tax.rs         (Phase 18) — integer bps + Decimal
├── sub_universe_allocator.rs  (Phase 18) — VPIN NaN guard
└── asian_exchange.rs          (Phase 19) — ASX DST dynamic

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — sequential steps
├── ouroboros/symbology_mapper.py  (Phase 8) — + reverse mapping
├── telegram_reporter.py       (Phase 17) — daemon=False, retry
├── pdf_generator.py           (Phase 17)
├── shadow_book.py             (Phase 17) — £50 threshold
├── cross_timezone.py          (Phase 21) — artifact validation
└── asia_universe.py           (Phase 21)

config/
├── european_exchange_profiles.toml  (Phase 18) — static exchange profiles
├── european_routing_table.toml      (Phase 18) — routing preferences
├── transaction_tax.toml             (Phase 18) — integer bps, all FTT/stamp duty
├── asian_exchange_profiles.toml     (Phase 19) — static exchange profiles
└── asian_routing_table.toml         (Phase 19) — routing preferences

calibration/
├── weights.json               (Ouroboros step 10) — ASER scoring weights
├── asia_cross_tz.json         (Ouroboros step 9 + updated_at timestamp) — DCC-GARCH weights
├── corp_action_blocklist.json (Ouroboros step 2 — atomic write) — corporate action vetoes
├── exchange_times.json        (Ouroboros step 1 — written at init) — dynamic session times incl. ASX DST
└── compaction_manifest.json   (Phase 22 — WAL compaction job) — preserved event IDs for open positions

NOTE: exchange_times.json is a CALIBRATION artifact written by Ouroboros (dynamic, changes with DST).
      exchange_profiles.toml files are STATIC config (human-edited, version-controlled). These are different files serving different purposes.
```

---

## TERMINAL KICKOFF PROMPT (Phase 8)

Paste this into a new Claude Code terminal session to begin Phase 8 implementation:

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v20.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v20.md

Implement all 15 SC items in order. Write unit tests for each. Run cargo test after each SC item before proceeding to the next.

SC-01: SIGTERM handler in main.rs — ctrlc crate, flatten positions → wait 30s for fills → write SystemShutdown WAL event → exit
SC-01a: docker-compose.yml — add `stop_grace_period: 60s` to the aegis-v2 service definition (v20-FIX-1)
SC-02: SubscriptionManager skeleton in subscription_manager.rs — use tokio::sync::Mutex (NOT std::sync::Mutex), deterministic cancel→ACK→subscribe protocol; confirmation via AtomicUsize line counter NOT 2-second silence heuristic (v20-FIX-7)
SC-03: LineBudget struct {carry: usize, active: usize, scan: usize} with hard assert!(carry + active + scan <= 100)
SC-04: Two-tier data architecture: (a) ibkr_broker.rs token bucket 60 req/10min, max 6 concurrent, exponential backoff on Error 162; (b) ouroboros/data_fetch.py uses Polygon.io for nightly 5000+ tickers; (c) separate Python token bucket for Ouroboros (does NOT share Rust bucket)
SC-05: MINIMUM_ENTRY_GBP: f64 = 1500.0 — pre-entry gate in risk_arbiter.rs. SUSPENDED when validated_trades_count < 250 (dynamic Kelly ramp period). Gate re-activates automatically at trade 250.
SC-06: Dust guard — FILLED portion < £500.0 → submit Peg-to-Mid limit order at (bid+ask)/2, TIF=3min; if not filled after 3min → submit market-sell; cancel unfilled remainder separately
SC-07: Fix V1 S3 contradiction — remove reactivation comment from mean_reversion.py
SC-08: APScheduler timezone audit in main.py — verify all pre-LSE jobs use timezone="Europe/London"
SC-09: crossbeam-channel bounded ring buffer (capacity=50000). On TrySendError::Full → DO NOT drop oldest tick. Instead: aggregate the overflow tick into the current OHLCV bar: bar.high = max(bar.high, tick.last); bar.low = min(bar.low, tick.last); bar.volume += tick.volume. Increment overflow_counter and log to WAL. (v20-FIX-13)
SC-10: Internal cost-basis tracker: CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }. avg_cost = total_cost / total_shares. VWAP averaging across partial fills. Nightly clear at Ouroboros step 1 + IBKR reqPositions resync.
SC-11: SubscriptionManager active_line_count: AtomicUsize — increment on reqMktData ACK, decrement on cancelMktData ACK; assert!(count <= 100) before every new subscription
SC-12: symbology_mapper.py — rules (a) space→dot, (b) LSE suffix→prefix, (c) exchange pass-through, (d) preferred shares BAC PR D → BAC/PD, (e) reverse mapping to_ibkr(polygon_symbol) for universe scan results
SC-13: (a) dynamic Kelly ramp: kelly_scale = max(0.1, min(1.0, validated_trades / 250.0)) in risk_arbiter.rs — floor of 0.1 ensures system places orders from day 1; min-entry gate suspended until trade 250; (b) POLARS_MAX_THREADS=2 environment variable in docker-compose.yml under the aegis-v2 service; (c) SplitAdjustment WalPayload variant added: resets Chandelier stop and cost basis on stock split
SC-14: reqMarketDataType(3) — add client.req_market_data_type(3) as THE FIRST CALL in ibkr_broker.rs::connect() before any subscribe_bars() or reqMktData calls (v20-FIX-8)
SC-15: StrategyId enum extension — add StrategyId::HotScanner and StrategyId::RotationScanner to types/enums.rs; verify WalPayload::PositionOpened and PositionClosed include strategy_id field (v20-FIX-10)

After all 15 items have passing tests:
- Run cargo test (all tests must pass)
- Run docker build (must succeed)
- Verify docker-compose.yml has stop_grace_period: 60s AND POLARS_MAX_THREADS=2
- Run a 30-minute paper session to verify SC-01 SIGTERM drill end-to-end
- Verify reqMarketDataType(3) appears as first IBKR call in paper session logs

Do NOT start Phase 11 until Phase 8 gate is fully signed off.
```

---

*AEGIS_MASTER_PLAN_v20.md — Generated 2026-03-09*
*Supersedes: AEGIS_MASTER_PLAN_v19.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v19.md (Claude 200-bullet audit + Gemini 200-bullet triage)*
*14 v20 fixes: 10 P0 fatal issues + 4 P1 high-severity issues from combined adversarial audit*
*Total acceptance tests: 226 (vs 194 in v19, +32 new tests covering v20 fixes)*
*Total remaining hours: 322h (vs 312h in v19, +10h for v20 additions)*
