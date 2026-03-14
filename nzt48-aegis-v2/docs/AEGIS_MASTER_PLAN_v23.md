# AEGIS V2 — MASTER PLAN v23
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 23.0 | **Date**: 2026-03-09 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v22. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v22.md — Claude's independent second-order adversarial audit of v22 (10 G4-P priority fixes + 3 G4-S structural fixes). New fixes are marked **[v23-FIX-N]** for traceability. The v23 audit attacked v22's own fixes for second-order consequences: wrong memory orderings, zero-divide guards, EVT parameter bounds, settlement lag omission, permit double-return, cache staleness, CRC32 edge cases, kickoff prompt contradiction, TS prior miscalibration, asyncio threading anti-pattern, watchdog absence, and WAL replay timeout absence.

---

## v23 DELTA — 13 CLAUDE G4 FIXES

| Fix | G4 ID | Trap | What was wrong in v22 | What v23 does |
|-----|-------|------|-----------------------|---------------|
| **v23-FIX-1** | G4-P1 | SeqCst over-ordering | v22-FIX-1 mandated `Ordering::SeqCst` for all AtomicUsize operations. SeqCst imposes global memory fence on every subscription ACK. The Semaphore(100) is the actual budget gate — AtomicUsize is telemetry only. SeqCst creates unnecessary cache-line contention at market open (hundreds of ACKs/min). | `fetch_add`/`fetch_sub`: `Ordering::AcqRel`. `load`: `Ordering::Acquire`. SeqCst removed from all AtomicUsize operations. Add comment clarifying Semaphore is the authority. |
| **v23-FIX-2** | G4-P2 | OFI zero-volume divide | v22-FIX-3 OFI formula `(Σbid_vol − Σask_vol) / (Σbid_vol + Σask_vol + ε)` with `ε = 1e-9` appears in kickoff prompt but NOT in Phase 8 deliverable text. On reconnect, first overflow window may have zero bid/ask volume → `0 / 0` (without ε) OR `0 / 1e-9 = 0.0` (with ε) → zero OFI triggers short-bias in EWMA. Must emit neutral 0.5 when both sums are zero. | SC-09 deliverable: explicit `+ 1e-9`. Zero-volume guard: `if bid_vol_sum == 0.0 && ask_vol_sum == 0.0 → emit ratio = 0.5 (neutral)`. Add AT-60b. |
| **v23-FIX-3** | G4-P3 | EVT GPD ξ unbounded | v22-FIX-9 EVT GPD formula `u + σ/(1-ξ) × ((n/k × α)^(-ξ) - 1) / ξ` undefined at ξ=0 and invalid at ξ≥1. Minimum threshold of 20 exceedances too low for stable MLE. | ξ=0 case: exponential CVaR formula. ξ clamp: `[-0.5, 0.5]` with `GpdShapeExcessive` WAL event. Minimum threshold: **50 exceedances** (not 20). Add AT-93c, AT-93d. |
| **v23-FIX-4** | G4-P4 | Settlement lag T+2 omitted | v22-FIX-7 normalized corp action ex-dates to exchange timezone, but vetoes on ex_date itself. Settlement is T+2 on all major exchanges — must hold by ex_date - 2 business days. TSE ex-date 2026-04-10 → must be out by 2026-04-08 close. | `settlement_lag_days: 2` added to EXCHANGE_TIMEZONE_MAP for all exchanges. Corp action veto date = ex_date_local − 2 business days (via reqTradingHours calendar). Add AT-111c. |
| **v23-FIX-5** | G4-P5 | PermitGuard double-return | v22-FIX-5 `SemaphorePermitGuard` with `Drop::drop() → add_permits(1)` is ambiguous about whether the underlying `OwnedSemaphorePermit` is also stored. If both tokio permit drop AND `add_permits(1)` fire, permits inflate above 100. | `SemaphorePermitGuard` stores `OwnedSemaphorePermit`. Drop: `std::mem::forget(permit)` then `semaphore.add_permits(1)`. Exactly one return path. Add AT-18c (100-permit cycle + async panic test). |
| **v23-FIX-6** | G4-P6 | Spread cache no staleness guard | v22-FIX-2 intraday_spread_cache.json written nightly. If Ouroboros fails 3+ nights (Polygon outage, IBC restart), SmartRouter uses week-old spreads and routes incorrectly. | Add `generated_at` Unix timestamp to cache. SmartRouter: if `now() - generated_at > 48h` → log `SpreadCacheStale` → force ETP routing for all direct equity candidates. Add AT-37c. |
| **v23-FIX-7** | G4-P7 | CRC32 sentinel ambiguity | v22-FIX-4 appends `{"_crc32": ...}` as last WAL line. Missing CRC32 line (truncated exactly at boundary) causes undefined behavior. Key `_crc32` could clash with future WAL fields. | CRC32 sentinel key: `{"__aegis_crc32__": "<hex>"}`. Missing CRC32 line → `ActiveStateNoCrc32` log → WAL replay (not silent load). Add AT-227c. |
| **v23-FIX-8** | G4-P8 | Kickoff prompt uses ctrlc crate | v22 terminal kickoff prompt SC-01 says `ctrlc crate` for SIGTERM handler. v21 full triage (G2-IN12) mandated replacing ctrlc with `tokio::signal` to eliminate ctrlc/tokio race condition. Kickoff prompt contradicts phase spec — implementer follows kickoff prompt. | Update terminal kickoff SC-01 to use `tokio::signal::ctrl_c()` + `tokio::signal::unix::signal(SignalKind::terminate())`. Explicit: `DO NOT use ctrlc crate`. |
| **v23-FIX-9** | G4-P9 | Thompson Sampler prior σ_0 too narrow for 3x ETPs | Phase 13 hardcodes `σ_0 = 0.05` prior for all assets. For QQQ3.L (atr_14_pct=0.067), TS converges prematurely after 3-4 lucky trades, over-allocating to volatile ETPs before returns have averaged out. | Dynamic σ_0: `max(0.05, atr_14_pct × 3.0)` per asset. Stored as `ts_prior_sigma_0` in asset_volatility.json (Ouroboros step 8). Add AT-56b. |
| **v23-FIX-10** | G4-P10 | asyncio restart in wrong thread context | v22-IN17 asyncio fix calls `asyncio.new_event_loop()` inside the exception handler of a dead event loop — undefined behavior in Python's asyncio. Inner coroutine RuntimeErrors not caught by outer handler. | Thread-based restart: `threading.Thread(target=lambda: asyncio.run(fetch_all_tickers())).start()`. Join with timeout. Not `new_event_loop()` in exception handler. Add AT-113b. |
| **v23-FIX-11** | G4-S1 | No internal watchdog | Engine heartbeat (v20-FIX-9) is written BY the engine. Deadlocked tokio runtime cannot write its own heartbeat. Detection latency: up to 30 minutes. Unmanaged 3x positions for 30 min = 3-5% equity risk. | `std::thread::spawn` watchdog (NOT tokio task). Checks `AtomicU64 last_tick_ts` every 60s. If stale >120s during market hours → `std::process::exit(1)` → Docker restart. Add SC-18-W to Phase 8. |
| **v23-FIX-12** | G4-S2 | PermitGuard panic test wrong context | v22-FIX-5 unit test uses `catch_unwind` (synchronous). SemaphorePermitGuard is used inside `async fn` / tokio tasks. `catch_unwind` does not verify async panic behavior. Must test via `tokio::spawn` panic. | Add async panic test: spawn 100 tasks each acquiring SemaphorePermitGuard then panicking via tokio::spawn → after JoinErrors resolve → `available_permits() == 100`. |
| **v23-FIX-13** | G4-S3 | WAL replay no timeout | WAL replay fallback (when active_state.wal fails) replays ALL historical WAL events. Large WAL = 30-60s replay. During replay: no tick processing, no Chandelier management, unmanaged positions at 3x leverage. | WAL replay timeout: 30s. If exceeded → `DrawdownTier::Orange` + `WalReplayTimeout` WAL event. If `last_compaction_ts > 7 days` AND fast-path fails → immediate Orange (skip replay). Add AT-227d. |

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

*(unchanged from v22)*

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

**V1 Critical Bugs (unchanged from v22):**

| ID | Severity | Module | Issue | Fix |
|----|----------|--------|-------|-----|
| **AEGIS 0-05** | CRITICAL | ml_meta_model.py | Regime encoding always returns -1; confidence circular feedback; 43.7% fabricated data | Disable entirely until J-01/J-02 fixed and N ≥ 200 real trades |
| **J-02** | CRITICAL | ml_meta_model.py | Regime map uses fictional keys ("bull"/"bear") not RegimeState enum | Remap to actual RegimeState enum values |
| **J-01** | CRITICAL | ml_meta_model.py | Confidence leaked as ML input feature | Remove confidence; replace with raw_indicator_count, spread_bps, time_since_regime_change |
| **S3 Contradiction** | LOW | mean_reversion.py | Hard veto on leveraged ETPs (correct) contradicted by V2.1 reactivation comment (wrong) | Remove reactivation comment in SC-07 |

---

### 1.2 V2 Rust Engine — Complete Module Inventory

**Status: Phases 1-7 COMPLETE. ~9,000 LOC. 147+ tests. All 98 P0+P1 stop-ship items resolved.**

*(unchanged from v22)*

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
| types/ (4 files) | 1000+ | 4 | COMPLETE — 10 enums, 2 newtypes, MarketTick, OrderIntent; HotScanner/RotationScanner added (v20-FIX-10) |
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

---

## PART 2 — COMBINED ADVERSARIAL AUDIT TRIAGE SUMMARY

### 2.1 Combined P0 + P1 Matrix (v19 + v20 + v21 + v22 + v23 fixes)

**P0 — Fatal (System Will Not Function):**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-1 | Docker SIGKILL at 10s vs 30s SIGTERM wait | `stop_grace_period: 60s` in docker-compose.yml | **v20-FIX-1, Phase 8** |
| P0-2 | Polars vCPU starvation → IBKR disconnect | `POLARS_MAX_THREADS=2` in docker-compose.yml | **v20-FIX-2, Phase 8** |
| P0-3 | Half-Kelly + Min Entry = 0 trades possible | Dynamic Kelly ramp: floor 0.1× at 0 trades | **v20-FIX-3, Phase 8/15** |
| P0-4 | WAL compaction severs open position history | Exclude open position events + nightly active_state.wal rewrite | **v20-FIX-4 + v21-FIX-9, Phase 22** |
| P0-5 | reqPnL 1-per-connection IBKR limit | Account-level reqPnL instead of reqPnLSingle | **v20-FIX-5, Phase 20** |
| P0-6 | clock.rs BST addition missing % 86400 | chrono-tz Europe::London | **v20-FIX-6, Phase 11** |
| P0-7 | RwLock writer starvation on active_line_count | **AtomicUsize(AcqRel/Acquire) + Semaphore(100)** — no lock for counting **(v23-FIX-1 refines v22-FIX-1)** | **Phase 8** |
| P0-8 | No reqMarketDataType(3) call in broker | Add as first call in ibkr_broker.rs::connect() | **v20-FIX-8, Phase 8** |
| P0-9 | Heartbeat only fires in DARK (22h gap) | Engine-side 30-min heartbeat Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent from WAL | Add HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |
| **P0-11** | **reqOpenOrders wrong API — Error 3200 ban** | **Remove reconciliation; use internal AtomicUsize only** | **v21-FIX-2, Phase 11** |
| **P0-12** | **Docker /dev/shm 64MB → Polars Bus error** | **shm_size: '2gb' in docker-compose.yml** | **v21-FIX-5, Phase 8** |
| **P0-13** | **bypass-permissions grants LLM root execution** | **accept-edits ONLY in AEGIS_IMPLEMENTATION_PLAN** | **v22-FIX-6, Process** |
| **P0-14** | **Engine deadlock: no internal watchdog** | **std::thread watchdog; stale >120s → process::exit(1)** | **v23-FIX-11, Phase 8** |

**P1 — High (System Will Fail in Common Conditions):**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | EOD auction spread cache routes SmartRouter to ETP always | 5-day median intraday spread; intraday_spread_cache.json; staleness guard >48h | **v22-FIX-2 + v23-FIX-6, Phase 12** |
| P1-2 | Telegram polling thread dies silently | Infinite retry loop with exponential backoff | Phase 17 |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker cache invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV allocation | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | QI suspension at market open loses peak alpha signal | Volume-weighted bid/ask aggregator; zero-volume → neutral 0.5 | **v22-FIX-3 + v23-FIX-2, Phase 8** |
| P1-6 | σ_noise 30-day lag punishes breakout ETPs | ATR percentile: max(0.02, atr_14_pct × 1.5) | **v22-FIX-10, Phase 13** |
| P1-7 | Corp action ex-date timezone wrong + settlement lag T+2 omitted | EXCHANGE_TIMEZONE_MAP + settlement_lag_days=2 | **v22-FIX-7 + v23-FIX-4, Phase 16** |
| P1-8 | WAL compaction unbounded file for mega-runners | Nightly active_state.wal rewrite (atomic) | **v21-FIX-9 + v22-FIX-4, Phase 22** |
| P1-9 | reqPnL parses manual holdings → carry loop crash | HashSet<conid> whitelist in CarryMonitor | **v21-FIX-10, Phase 20** |
| P1-10 | Cornish-Fisher domain violation during flash crash | Maillard K>S²-1; EVT POT GPD; ξ bounds; ≥50 exceedances | **v21-FIX-3 + v22-FIX-9 + v23-FIX-3, Phase 15** |
| P1-11 | Cost basis wrong after overnight split | Nightly clear + IBKR reqPositions resync | Phase 8 |
| P1-12 | Dust market-sell slippage on illiquid | Peg-to-Mid limit, 3min TIF | Phase 8 |
| P1-13 | AtomicUsize leaks on dropped ACK | Internal tracking only; no reqOpenOrders | **v21-FIX-2, Phase 11** |
| P1-14 | FTT intraday exemption lost on carry | Flag FTT entries as no-carry eligible | Phase 18/20 |
| P1-15 | NZX misses opening auction daily | Pre-subscribe NZX at 22:55 UTC in DARK | Phase 19 |
| P1-16 | ISA tax year Jan 1 vs April 6 | Fix isa_gate.rs boundary to April 6 | Phase 12 |
| P1-17 | HKEX board lot → 0-share order | Fallback to ETP when lot×price > Kelly | Phase 12 |
| P1-18 | Polars parallel step execution → OOM | Enforce sequential step execution | Phase 16 |
| P1-19 | Carry allocator wrong — assumes 3 not 6 | Dynamic: available = 100 − (carry_count × 2) | **v20-FIX-14, Phase 20** |
| P1-20 | Semaphore permit leak on task panic | SemaphorePermitGuard — explicit forget+add_permits | **v22-FIX-5 + v23-FIX-5/12, Phase 8** |
| P1-21 | active_state.wal non-atomic write → corrupt state on restart | tmp + CRC32 + atomic rename; sentinel key; no-CRC32 fallback | **v22-FIX-4 + v23-FIX-7, Phase 22** |
| P1-22 | WAL replay no timeout → unmanaged positions | 30s replay budget; Orange tier on timeout | **v23-FIX-13, Phase 22** |
| P1-23 | Thompson Sampler σ_0 too narrow for 3x ETPs | Dynamic σ_0 = max(0.05, atr_14_pct × 3.0) | **v23-FIX-9, Phase 13** |

---

### 2.2 Binding Architectural Mandates (v19 + v20 + v21 + v22 + v23 additions)

*(v22 mandates retained; v23 additions shown)*

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Use Polars `LazyFrame` + Arrow zero-copy. 500-ticker batches. RSS ceiling 3.5GB. | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** `crossbeam-channel` bounded (capacity=50,000). Overflow → **volume-weighted bid/ask aggregator for OFI** (neutral 0.5 on zero-volume). Chandelier aggregates H/L/V. **(v23-FIX-2 extends v22-FIX-3)** | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox fix.** IBKR `reqHistoricalData` token bucket for active ~100 tickers ONLY. Nightly 5,000+ ticker universe → Polygon.io/Databento. | Phase 8 + 16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed ONLY when live position exists. | Phase 11 |
| **GEM-A5** | **Drawdown tier nomenclature.** Yellow/Orange/Red. Ouroboros failure → Yellow. | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time calculations via `chrono_tz::Europe::London`. | Phase 11 |
| **v20-A2** | **AtomicUsize(AcqRel/Acquire) for active_line_count + Semaphore(100) for budget.** No RwLock. SemaphorePermitGuard: stores OwnedSemaphorePermit, Drop uses `mem::forget + add_permits(1)`. **(v23-FIX-1 + v23-FIX-5 refine v22)** | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler with ATR-percentile σ_noise AND dynamic σ_0.** σ_noise = max(0.02, atr_14_pct × 1.5). σ_0 = max(0.05, atr_14_pct × 3.0) per asset. **(v23-FIX-9 extends v22-FIX-10)** | Phase 13 |
| **v20-A4** | **Account-level reqPnL only + CarryMonitor whitelist + UnauthorizedPnLStream alert.** Never use `reqPnLSingle`. | Phase 20 |
| **v21-A1** | **No reqOpenOrders for line reconciliation.** Internal AtomicUsize tracking only. | Phase 11 |
| **v21-A2** | **shm_size: '2gb' in docker-compose.yml.** | Phase 8 |
| **v21-A3** | **Maillard (2012) CF domain check + EVT POT fallback.** If K <= S²-1 AND ≥**50** tail exceedances → GPD fit with ξ bounds [-0.5, 0.5]; ξ=0 → exponential formula. **(v23-FIX-3 extends v22-FIX-9)** | Phase 15 |
| **v21-A4** | **Volume-weighted OFI aggregator on overflow.** Zero-volume guard: ratio=0.5 (neutral). **(v23-FIX-2 extends v22-FIX-3)** | Phase 8 |
| **v21-A5** | **active_state.wal nightly rewrite — atomic.** Sentinel key `__aegis_crc32__`. Missing CRC32 → WAL replay. **(v23-FIX-7 refines v22-FIX-4)** | Phase 22 |
| **v22-A1** | **EXCHANGE_TIMEZONE_MAP + settlement_lag_days=2.** Corp action veto = ex_date_local − 2 business days. **(v23-FIX-4 extends v22-FIX-7)** | Phase 16 |
| **v22-A2** | **intraday_spread_cache.json (5-day median intraday spread) + staleness guard >48h.** **(v23-FIX-6 extends v22-FIX-2)** | Phase 12/16 |
| **v22-A3** | **accept-edits ONLY in implementation tooling.** No bypass-permissions. | Process |
| **v23-A1** | **std::thread watchdog (NOT tokio task).** Checks AtomicU64 last_tick_ts every 60s. Stale >120s during market hours → `std::process::exit(1)`. Independent of tokio runtime. | Phase 8 |
| **v23-A2** | **WAL replay timeout 30s.** Exceeded → DrawdownTier::Orange. last_compaction_ts > 7 days + fast-path fail → immediate Orange. | Phase 22 |

---

### 2.3 Deferred (Post-Crucible)

*(unchanged from v22 + v23 academic items added)*

| Finding | Reason |
|---------|--------|
| Multi-level OFI (5-level depth) | Requires IBKR Level 2 subscription |
| EWMA correlation on VIX trip (vs binary ρ=1.0) | Enhancement, post-Crucible |
| HTB fee in SmartRouter | Data source needed; Phase Q2+ |
| Cryptographic Dead Man's Switch | Overkill at current scale |
| Bloomberg holiday calendars | reqTradingHours sufficient for Q1 |
| Chandelier non-linear decay | Phase Q2+ enhancement |
| Savitzky-Golay filter on QI | Phase Q2+ signal research |
| KRX VI post-momentum exploit | Phase Q2+ alpha research |
| t-DCC-GARCH | Phase Q2+ |
| HSMM regimes | Phase Q2+ |
| EKF Kalman filter | Phase Q2+ |
| Full Kelly theoretical maximum drawdown | Dynamic ramp prevents full Kelly until trade 250 |
| Nordic dark pool routing | Phase Q2+ |
| SGX SiMS TIF flags | Phase Q2+ |
| Lot-level cost basis (tax lot accounting) | VWAP cost basis sufficient for operations; CGT reporting is post-live |
| GPD scale parameter numerical stability (Newton-Raphson) | Phase Q2+ numerical methods |
| Multi-asset EVT correlation (CoVaR) | Phase Q2+ |
| Bayesian model averaging CF/EVT/Gaussian | Phase Q2+ |
| OFI decay function analysis | Phase Q2+ signal research |
| Chaos: simultaneous multi-failure scenarios | Phase Q2+ |

---

## PART 3 — PHASE PLAN

### Numbering Convention

- **Phases 1-7**: COMPLETE (V2 Rust core)
- **Phase 8**: Pre-conditions and P0 hardening (NEXT) — **18 SC items + v23 amendments**
- **Phases 9-10**: Reserved for future use
- **Phases 11-23**: Granular build phases

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 49.5h | **Status**: NEXT — must complete before Phase 11
*(+1.5h vs v22 for v23-FIX-11 watchdog thread, ordering corrections, guard pattern clarification)*

**Rationale**: Foundation hardening. v23 amendments: SC-02 ordering corrected from SeqCst to AcqRel/Acquire (v23-FIX-1); SC-02 SemaphorePermitGuard explicit pattern (v23-FIX-5/12); SC-09 zero-volume guard (v23-FIX-2); SC-18-W watchdog thread added (v23-FIX-11); kickoff prompt SC-01 corrected from ctrlc to tokio::signal (v23-FIX-8).

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: **`tokio::signal::ctrl_c()` + `tokio::signal::unix::signal(SignalKind::terminate())`** → flatten positions → 30s fill wait → WAL SystemShutdown event → exit. **DO NOT use ctrlc crate — races with tokio runtime (G2-IN12). (v23-FIX-8)** | main.rs | v23-FIX-8 |
| **SC-01a** | `stop_grace_period: 60s` added to docker-compose.yml **(v20-FIX-1)** | docker-compose.yml | v20-FIX-1 |
| **SC-02** | SubscriptionManager skeleton: **`AtomicUsize`** for `active_line_count`. Memory ordering: `fetch_add(1, Ordering::AcqRel)` on ACK, `fetch_sub(1, Ordering::AcqRel)` on cancel ACK, `load(Ordering::Acquire)` for reads. **NOT SeqCst — Semaphore(100) is the budget authority; AtomicUsize is telemetry only.** Add code comment: `// Semaphore(100) enforces budget; AtomicUsize is telemetry. AcqRel sufficient.` **(v23-FIX-1 — refines v22-FIX-1)**. **`tokio::sync::Semaphore(100)`** for the ≤100 budget constraint. **`SemaphorePermitGuard`** implementation: (a) stores `OwnedSemaphorePermit` via `Semaphore::acquire_owned()`, (b) Drop impl: `std::mem::forget(self.permit.take().unwrap())` then `self.semaphore.add_permits(1)`. Exactly one return path — tokio's built-in permit return is bypassed via `mem::forget`. Unit test AT-18b: 1000 concurrent subscribe/cancel sequences → active_line_count never exceeds 100. Unit test AT-18c (ASYNC panic): `tokio::spawn` 100 tasks each acquiring SemaphorePermitGuard then panicking via `panic!("deliberate")` → join all JoinErrors → verify `semaphore.available_permits() == 100`. **(v23-FIX-5 + v23-FIX-12 — clarifies v22-FIX-5)** | subscription_manager.rs | v23-FIX-1, v23-FIX-5, v23-FIX-12 |
| SC-03 | LineBudget struct `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket (60 req/10min, 6 concurrent, Error 162 backoff) for active ~100 tickers; Polygon.io/Databento for nightly 5,000+ universe | ibkr_broker.rs + ouroboros/data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` pre-entry gate in risk_arbiter.rs — **suspended during dynamic Kelly ramp below 250 trades** **(v20-FIX-3)** | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard: if `filled_gbp < 500.0` → submit Peg-to-Mid limit order at mid-price, TIF=3min; if not filled in 3min → submit market-sell; cancel unfilled remainder separately | exit_engine.rs | v19-FIX-1 |
| SC-07 | Fix V1 S3 contradiction: remove conflicting reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler timezone audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| **SC-09** | `crossbeam-channel` bounded ring buffer (capacity=50,000). On `TrySendError::Full` → **dual handling:** (a) OFI path: **volume-weighted aggregator** — accumulate `bid_vol_sum` and `ask_vol_sum`; emit OFI ratio = (bid_vol_sum − ask_vol_sum) / (bid_vol_sum + ask_vol_sum + **1e-9**); **ZERO-VOLUME GUARD: if bid_vol_sum == 0.0 && ask_vol_sum == 0.0 → emit ratio = 0.5 (neutral, not 0.0) to prevent short-bias. (v23-FIX-2)**; OFI EWMA remains live; (b) Chandelier path: aggregate H/L/V into current bar (bar.high=max, bar.low=min, bar.volume+=); Emit `QuoteImbalanceCompressed { ticker_id, bid_vol_sum, ask_vol_sum, dropped_count }` WAL event. Unit test AT-60: inject 200 overflow ticks with known bid_vol/ask_vol → verify emitted OFI ratio matches manual (Σbid_vol − Σask_vol)/(Σbid_vol + Σask_vol) ± 0.001. Unit test AT-60b: inject 50 overflow ticks with bid_vol=0.0 AND ask_vol=0.0 → emitted ratio = 0.5 (NOT 0.0). **(v23-FIX-2 extends v22-FIX-3)** | python_bridge.rs + channel.rs + types/wal.rs | v23-FIX-2 |
| SC-10 | Internal cost-basis tracker: `HashMap<TickerId, CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }>`. VWAP cost basis. Nightly clear + IBKR reqPositions resync at Ouroboros step 1. | portfolio.rs | G-09 |
| SC-11 | SubscriptionManager `active_line_count: AtomicUsize`; increment on `reqMktData` ACK (`fetch_add(1, Ordering::AcqRel)`), decrement on `cancelMktData` ACK (`fetch_sub(1, Ordering::AcqRel)`); `assert!(count <= 100)` before every new subscription. **No reqOpenOrders reconciliation — internal tracking only. (v21-FIX-2)** | subscription_manager.rs | v21-FIX-2 |
| SC-12 | `symbology_mapper.py`: rules: (a) space→dot; (b) LSE suffix→prefix; (c) exchange pass-through; (d) preferred shares `BAC PR D → BAC/PD`; (e) reverse mapping `to_ibkr(polygon_symbol)` | ouroboros/symbology_mapper.py | v19-FIX-2 |
| **SC-13** | Dynamic Kelly ramp **(v20-FIX-3):** `kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))`. Add `POLARS_MAX_THREADS=2` to docker-compose.yml env. `SplitAdjustment` WAL event added. | risk_arbiter.rs + docker-compose.yml + types/wal.rs | v20-FIX-3 |
| **SC-14** | `reqMarketDataType(3)` first call **(v20-FIX-8)** | ibkr_broker.rs | v20-FIX-8 |
| **SC-15** | StrategyId enum extension **(v20-FIX-10):** Add `StrategyId::HotScanner` and `StrategyId::RotationScanner` to `types/enums.rs`. | types/enums.rs + types/wal.rs | v20-FIX-10 |
| **SC-16** | **`shm_size: '2gb'` in docker-compose.yml (v21-FIX-5)** | docker-compose.yml | v21-FIX-5 |
| **SC-17** | **`WalPayload::QuoteImbalanceCompressed` variant** with fields `ticker_id: TickerId, bid_vol_sum: f64, ask_vol_sum: f64, dropped_count: u32`. Wire into channel.rs overflow path. **(v22-FIX-3)** | types/wal.rs | v22-FIX-3 |
| **SC-18-W** | **Watchdog thread (v23-FIX-11):** `std::thread::spawn` (NOT tokio task) a watchdog. The watchdog thread loops every 60 seconds. Reads `AtomicU64 LAST_TICK_TS` (updated by market data handler on every received tick via `LAST_TICK_TS.store(unix_ts_secs, Ordering::Relaxed)`). During market hours (`clock.is_lse_open()` OR `clock.is_apac_hours()`): if `now() - last_tick_ts > 120` → log to stderr `"[WATCHDOG] No tick in 120s during market hours — DEADLOCK SUSPECTED. Exiting."` → `std::process::exit(1)`. Docker restart policy (`restart: unless-stopped`) brings engine back. The watchdog writes to a SEPARATE file `/app/logs/watchdog.log` using std::fs (NOT the WAL writer, which may itself be deadlocked). The watchdog does NOT use any tokio runtime primitive — pure `std::thread`. | main.rs (global AtomicU64) + engine.rs (tick update) + watchdog.rs (new file) | v23-FIX-11 |

**Gate**: All 18 items coded + unit tested; `cargo test` passes; `docker build` passes; crossbeam dual-path overflow verified; SemaphorePermitGuard async panic test passes (100 tokio::spawn panics → available_permits == 100); AtomicUsize uses AcqRel/Acquire (grep: no SeqCst in subscription_manager.rs); OFI zero-volume guard passes (AT-60b: ratio==0.5); watchdog thread spawned and logs confirmed; tokio::signal used (grep: no ctrlc crate); docker-compose.yml has `stop_grace_period: 60s`, `POLARS_MAX_THREADS=2`, `shm_size: '2gb'`

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 22h | **Depends on**: Phase 8
*(unchanged from v22 except AtomicUsize ordering refinement propagated)*

**v23 Amendment:** SC-02 AtomicUsize ordering is AcqRel/Acquire (not SeqCst). All Phase 11 subscription_manager.rs code uses `AcqRel` for writes and `Acquire` for reads. SemaphorePermitGuard explicit pattern (forget + add_permits) applies throughout. The Semaphore(100) is the sole budget authority.

**Deliverables:**

- `clock.rs` REWRITTEN — chrono-tz **(v20-FIX-6):**
  - `use chrono_tz::Europe::London;`
  - `fn now_london() -> DateTime<London>` — authoritative London local time
  - `TradingMode` enum: `{ModeA, ModeB, ModeBPlus, ModeC, Dark}`
  - Cargo.toml: add `chrono-tz = "0.9"` dependency

- `subscription_manager.rs` (NEW, extends SC-02/SC-03/SC-11 skeleton):
  - Full `AtomicUsize` (AcqRel writes, Acquire reads) for `active_line_count` **(v23-FIX-1)**
  - `tokio::sync::Semaphore(100)` for line budget
  - `SemaphorePermitGuard` with `mem::forget + add_permits(1)` Drop **(v23-FIX-5)**
  - **No reqOpenOrders (v21-FIX-2)**
  - Proptest: 500 random subscribe/cancel sequences, invariant holds
  - **Scanner Conservation Rule (GEM-A4)**

- `mode_controller.rs` (NEW): 5-mode state machine

- **NZX pre-subscribe at 22:55 UTC**

**Acceptance Tests (AT-01 to AT-18):**
- AT-01 through AT-16: same as v22
- AT-17: Leap year chrono-tz Feb 29 2028
- **AT-18: No reqOpenOrders (grep). No RwLock (grep). No SeqCst in subscription_manager.rs (grep).**
- **AT-18b: AcqRel ordering verified (grep for AcqRel in subscription_manager.rs → present)**
- **AT-18c: SemaphorePermitGuard async panic test (100 tokio::spawn panics → available_permits == 100)**

**Gate**: 20 tests pass; chrono-tz DST flip verified; grep confirms AcqRel (not SeqCst) and no RwLock and no reqOpenOrders; SemaphorePermitGuard async panic test passes

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 21.5h | **Depends on**: Phase 11
*(+0.5h vs v22 for spread cache staleness guard + AT-37c)*

**v23 Amendments:**

- **Spread cache staleness guard (v23-FIX-6):** SmartRouter: on loading `intraday_spread_cache.json`, check `generated_at` Unix timestamp. If `now() - generated_at > 48 * 3600` → log `SpreadCacheStale { age_hours: f64 }` → set internal flag `spread_cache_stale = true` → all routing decisions for direct equity fall back to ETP until next valid cache load. Smart router still runs; it just forces ETP path.

**Deliverables:**

- `smart_router.rs` (NEW):
  - **Intraday spread cache lookup (v22-FIX-2 + v23-FIX-6):** Load `intraday_spread_cache.json`. Check `generated_at`. If stale >48h → ETP fallback for all. Zero-spread guard: `if cached_intraday_spread_bps == 0.0 → ETP fallback`.
  - Route logic: `if !spread_cache_stale && cached_intraday_spread_bps > 0.0 && ...`
  - All other routing logic unchanged from v22.

- `isa_gate.rs` (NEW): April 6 boundary, Taiwan/China/India hard-block

**Acceptance Tests (AT-19 to AT-41):**
- AT-19 through AT-40: same as v22
- **AT-37c: Staleness guard: load cache with generated_at = 72h ago → SpreadCacheStale logged; ALL routing decisions return ETP; direct equity routes = 0**

**Gate**: 24 tests pass; staleness guard verified (AT-37c); intraday spread cache non-empty; zero-spread guard verified

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 25.5h | **Depends on**: Phase 12
*(+0.5h vs v22 for dynamic σ_0 + AT-56b)*

**v23 Amendments:**

- **Dynamic Thompson Sampler prior σ_0 (v23-FIX-9):** `rotation_scanner.rs` reads `ts_prior_sigma_0` from `asset_volatility.json` (written by Ouroboros step 8). If absent → use floor `σ_0 = 0.05`. This initialization runs once when asset first enters the TS; it is NOT recalculated per-tick. Formula: `σ_0 = max(0.05, atr_14_pct × 3.0)`. For QQQ3.L (atr_14_pct≈0.067): `σ_0 = 0.201`. For ASML (atr_14_pct≈0.01): `σ_0 = 0.05` (floor). Wider prior prevents premature convergence on high-volatility leveraged ETPs.

**Deliverables:**

- `hot_scanner.rs` (NEW): QuoteImbalance EWMA, CUSUM, Kalman, meta-label gate 0.55
  - **QI resume: reset EWMA to 0.5 after overflow_counter == 0 for ≥5s (v22-M2)**
  - **Zero-volume OFI → EWMA input = 0.5 (neutral) (v23-FIX-2)**

- `rotation_scanner.rs` (NEW):
  - **Gaussian-Gaussian Thompson Sampler:** `σ_noise = max(0.02, atr_14_pct × 1.5)` **(v22-FIX-10)**
  - **Dynamic prior: `σ_0 = max(0.05, atr_14_pct × 3.0)` from asset_volatility.json **(v23-FIX-9)**
  - Prior: `μ_0 = 0.0`; `σ_0` = dynamic per asset
  - Hard slot limit: max 40 HotScanner + 10 RotationScanner

- `universe_scanner.rs` (NEW): ADV filter, RVOL calc, 100-line budget respect

**Acceptance Tests (AT-41 to AT-61):**
- AT-41 through AT-58: same as v22
- **AT-56b: Dynamic σ_0: QQQ3.L with atr_14_pct=0.067 → ts_prior_sigma_0 in asset_volatility.json = 0.201; TS initializes with σ_0=0.201; ASML with atr_14_pct=0.01 → σ_0=0.05 (floor)**
- AT-59: QI neutral resume
- AT-60: Volume-weighted OFI ratio ±0.001
- AT-60b: Zero-volume overflow → ratio=0.5

**Gate**: 21 tests pass; dynamic σ_0 loaded from asset_volatility.json; QI neutral-state resume verified; zero-volume OFI guard verified

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 22h | **Depends on**: Phase 13
*(unchanged from v22)*

**Deliverables:**
- `exit_engine.rs` EXTENDED — Infinite Chandelier with 8 adaptive multipliers
- `executioner_v2.rs` (NEW): ADV gate, U-shaped TWAP, TWAP cancel on Chandelier hit
- `spread_veto.rs` (NEW): U-shaped intraday spread tolerance

**Acceptance Tests (AT-61 to AT-80):** same as v22.

**Gate**: 15 tests pass; Chandelier-TWAP interaction verified

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 21h | **Depends on**: Phase 14
*(+1h vs v22 for EVT ξ bounds, exponential formula, ≥50 threshold, AT-93c/93d)*

**v23 Amendments:**

- **EVT GPD ξ bounds (v23-FIX-3):** When K <= S²-1 AND ≥**50** exceedances above 95th pct threshold `u`:
  1. Fit GPD via MLE on exceedances: estimate `σ` (scale) and `ξ` (shape)
  2. **ξ=0 special case**: `if xi.abs() < 1e-6 { CVaR_exp = u + sigma × (1.0 - ln(k as f64 / (n as f64 × alpha))) }` — exponential tail formula, no division by ξ
  3. **ξ clamp**: `xi = xi.clamp(-0.5, 0.5)`. If MLE returned |ξ| > 0.5 → log `WalPayload::GpdShapeExcessive { xi_mle: f64, xi_clamped: 0.5 }`. Proceed with clamped value.
  4. GPD CVaR formula (general case): `CVaR_GPD = u + σ/(1-ξ) × ((n/k × α)^(-ξ) - 1) / ξ`
  5. **Threshold**: ≥50 exceedances required (not 20). If 20 ≤ exceedances < 50: Gaussian CVaR. If < 20 exceedances: Gaussian CVaR (same as v22, tighter upper bound).

**Deliverables:**

- `risk_arbiter.rs` EXTENDED — 31 vetoes
- `cvar_heat.rs` (NEW):
  - **CF expansion gated: N≥20 AND |S|<2 AND K > S²-1 (v21-FIX-3)**
  - **EVT POT GPD fallback with ξ bounds and ≥50 exceedance threshold (v23-FIX-3)**
  - CVaR limit scales with `kelly_scale`
  - VIX circuit breaker with startup blind spot protection
  - CVaR max-correlation damping factor 0.8

**Acceptance Tests (AT-76 to AT-100):**
- AT-76 through AT-93b: same as v22 (AT-93b uses 30 exceedances — now correctly above 50? No — AT-93b in v22 used 30 exceedances. Update: AT-93b now tests ≥50 exceedances for GPD path)
- **AT-93b UPDATE**: EVT POT path: K <= S²-1, **55** exceedances above 95th pct → GPD fit applied; CVaR_GPD > Gaussian CVaR by ≥20%. (20 exceedances now routes to Gaussian, not GPD)
- **AT-93c: ξ=0 case: MLE converges to ξ < 1e-6 → exponential CVaR formula used; no division by zero; result finite**
- **AT-93d: ξ excessive: MLE converges to ξ=1.8 → clamped to 0.5; GpdShapeExcessive WAL event logged; CVaR computed with ξ=0.5**
- AT-94: CVaR-Kelly scaling
- AT-95: VIX blind at startup

**Gate**: 24 tests pass; GPD ξ=0 case verified; ξ clamp verified with WAL event; ≥50 threshold enforced; 31 total vetoes confirmed

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 28h | **Depends on**: Phase 15
*(+2h vs v22 for settlement lag in step 2, ts_prior_sigma_0 in step 8, threading fix for asyncio, AT-111c + AT-113b)*

**v23 Amendments:**

- **Settlement lag T+2 in corp action blocklist (v23-FIX-4):** Ouroboros step 2: after computing `ex_date_local` via EXCHANGE_TIMEZONE_MAP, compute `veto_date = ex_date_local - settlement_lag_days` where `settlement_lag_days = EXCHANGE_TIMEZONE_MAP[exchange]["settlement_lag_days"]` (all major exchanges: 2 business days). Use `veto_date` for `corp_action_blocklist.json`. This prevents holding a position into settlement on ex-date. Use `reqTradingHours` exchange calendar (already fetched in step 1) for business day arithmetic.

- **ts_prior_sigma_0 in Ouroboros step 8 (v23-FIX-9):** Compute `ts_prior_sigma_0 = max(0.05, atr_14_pct × 3.0)` per asset from tick data. Write to `calibration/asset_volatility.json` alongside `atr_14_pct` and `atr_14_abs`.

- **Thread-based asyncio restart in data_fetch.py (v23-FIX-10):** Replace `asyncio.new_event_loop()` in-handler with: catch `RuntimeError` → log → `threading.Thread(target=lambda: asyncio.run(fetch_all_tickers()), daemon=True).start()` → join with `thread.join(timeout=300)` (5-minute budget). If join times out → log `DataFetchThreadTimeout` → proceed to step 3 with stale/empty data (Ouroboros is best-effort nightly).

**Deliverables:**

- `ouroboros/` EXTENDED — 10-step pipeline:
  1. **Data fetch** — Polygon.io + IBKR active tickers; nightly cost basis clear + reqPositions resync
  2. **Corporate action blocklist** — Polygon.io; **EXCHANGE_TIMEZONE_MAP + settlement_lag_days=2; veto_date = ex_date_local - 2 business days (v23-FIX-4)**; atomic write
  3. **Universe discovery** — 5,000+ tickers; 5-day median INTRADAY spread; write `intraday_spread_cache.json` with `generated_at` timestamp **(v22-FIX-2 + v23-FIX-6)**; Polars LazyFrame 500-ticker batches
  4. **Feature engineering** — Polars LazyFrame; write to /dev/shm during processing
  5. **Scoring** — ASER: momentum 30%, liquidity 20%, volatility 20%, regime 15%, recency 15%
  6. **Meta-label training** — Logistic Regression / LightGBM fallback
  7. **Chandelier calibration** — ATR, MAE/MFE profiling
  8. **Thompson Sampling update** — Gaussian-Gaussian posteriors; compute `atr_14_pct` AND **`ts_prior_sigma_0 = max(0.05, atr_14_pct × 3.0)`** per asset; write `asset_volatility.json` **(v23-FIX-9)**
  9. **DCC-GARCH update** — cross-asset correlation matrix
  10. **PDF generation + artifact write + Telegram ALIVE** — active_state.wal write (v22-FIX-4 / v23-FIX-7 atomic pattern)

- **asyncio RuntimeError: thread-based restart (v23-FIX-10)** in data_fetch.py

**Acceptance Tests (AT-98 to AT-122):**
- AT-98 through AT-111b: same as v22
- **AT-111c: HKEX corp action veto: ex_date_local=2026-04-10 HKT; settlement_lag_days=2; veto_date=2026-04-08 HKT; blocklist entry uses 2026-04-08**
- **AT-112: intraday_spread_cache.json includes `generated_at` timestamp field; value is within 60s of step 3 execution time**
- **AT-113: asset_volatility.json includes `ts_prior_sigma_0` field; QQQ3.L value ≈ 0.201 (3× atr_14_pct ≈ 0.067)**
- **AT-113b: asyncio thread restart: simulate RuntimeError in data_fetch.py inner coroutine → thread restarted → fetch completes → Ouroboros pipeline continues; DataFetchThreadTimeout logged if thread exceeds 300s**

**Gate**: 25 tests pass; EXCHANGE_TIMEZONE_MAP + settlement_lag_days verified with HKEX T+2 test; generated_at in spread cache verified; ts_prior_sigma_0 in asset_volatility.json verified; asyncio thread restart verified

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 15h | **Depends on**: Phase 16
*(unchanged from v22 except AT-130 updated to match threading pattern)*

**Deliverables:** Same as v22. async Redis heartbeat, Telegram HALT auth, asyncio safe restart in both telegram_reporter.py (thread-based) and data_fetch.py (thread-based per v23-FIX-10).

**AT-130 UPDATE:** asyncio RuntimeError in data_fetch.py: simulate closed loop inside nested coroutine → **new daemon thread spawned** (not new_event_loop in handler) → fetch completes. Join with 300s timeout.

**Gate**: 16 tests pass; thread-based asyncio restart verified in both files

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 21h | **Depends on**: Phase 17
*(unchanged from v22)*

**Gate**: 25 tests pass; UK ISIN stamp duty verified; 5 paper trading days

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21h | **Depends on**: Phase 18
*(unchanged from v22)*

**Gate**: 20 tests pass; JPY decimal precision verified; reconnect 15s delay verified

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 24h | **Depends on**: Phase 19
*(unchanged from v22)*

**Gate**: 25 tests pass; UnauthorizedPnLStream Telegram alert verified (single per conid)

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13h | **Depends on**: Phase 20
*(unchanged from v22)*

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 34.5h | **Depends on**: Phase 21
*(+1.5h vs v22 for CRC32 sentinel fix, no-CRC32 handling, WAL replay timeout)*

**v23 Amendments:**

- **active_state.wal CRC32 sentinel key (v23-FIX-7):** Use `{"__aegis_crc32__": "<hex>"}` (not `{"_crc32": ...}`). The sentinel key `__aegis_crc32__` cannot appear in any normal WAL payload (WAL keys use snake_case without double underscores). On load: strip last line; if last line does not match sentinel pattern → `ActiveStateNoCrc32` event → WAL replay (same path as mismatch). Never silently load without verification.

- **WAL replay timeout (v23-FIX-13):** `const WAL_REPLAY_TIMEOUT_SECS: u64 = 30`. Start a `std::thread` timer on entry to WAL replay. If replay exceeds 30s → send DrawdownTier::Orange signal to engine → log `WalReplayTimeout { elapsed_secs: u64 }` → continue with empty position state (assumes flat). Additionally: read `last_compaction_ts` from `compaction_manifest.json`. If `now() - last_compaction_ts > 7 * 86400` AND `active_state.wal` fast-path fails → immediately DrawdownTier::Orange without attempting replay (WAL too stale to be useful).

**Deliverables:**

- **active_state.wal atomic write pattern (v22-FIX-4 + v23-FIX-7):** tmp + CRC32 with sentinel key `__aegis_crc32__` + os::rename on write; CRC32 verify on load; missing sentinel → `ActiveStateNoCrc32` → WAL replay; mismatch → `ActiveStateCorrupt` → WAL replay
- **WAL replay timeout 30s (v23-FIX-13):** Orange tier on timeout; 7-day stale compaction → immediate Orange
- **V2 calibration S3 backup cron (v22-IN7)**
- **ArcSwap exchange-hours validation on SIGHUP (v21)**
- **PDF cleanup cron (v21)**
- All v22 deliverables retained

**Acceptance Tests (AT-216 to AT-234):**
- AT-216 through AT-227b: same as v22
- **AT-227c: No-CRC32 guard: write active_state.wal without `__aegis_crc32__` sentinel line → engine detects missing sentinel → logs ActiveStateNoCrc32 → falls back to WAL replay**
- **AT-227d: WAL replay timeout: inject synthetic WAL with 10,000+ events; if replay exceeds 30s → DrawdownTier::Orange fires; WalReplayTimeout logged; position state assumed flat**
- AT-228: active_state.wal stale fallback
- AT-229: SIGHUP config reload rejection
- AT-230: PDF cleanup cron 30-day retention
- AT-231: S3 backup cron dry-run

**Gate**: 25 tests pass; CRC32 sentinel key correct (`__aegis_crc32__`); no-CRC32 fallback verified (AT-227c); WAL replay timeout fires at 30s (AT-227d); 7-day stale → immediate Orange verified; 48h continuous paper run

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(Romano-Wolf single-hypothesis correction retained from v20)*

> **The Engineering-vs-Alpha Boundary:** Phases 8-22 eliminate every known infrastructure failure mode. Phase 23 tests whether the signal has edge after clean infrastructure.
>
> **If WR ≥ 40% and Sharpe > 0:** Signal has genuine edge. Live capital granted.
> **If WR < 40% or Sharpe ≤ 0:** Signal math must be rewritten. Signal Rewrite Protocol activates.

**Suite 7 — Full Mode Cycle (updated for v23):**
- 24h paper run: all mode transitions
- Ouroboros completes all 10 steps including settlement-lag-corrected corp action blocklist
- intraday_spread_cache.json staleness check: cache must be < 48h old on Suite 7 start
- asset_volatility.json includes `ts_prior_sigma_0` for all active assets
- Watchdog thread confirmed running (`ps aux` in container shows watchdog process)

**Gate**: All 7 suites pass with written sign-off. 100 validated paper trades. No P0 bugs open. **APPROVED FOR LIVE CAPITAL** stamp.

> **Signal Rewrite Protocol (if Crucible fails):** If WR < 40% after clean 100-trade run with zero infrastructure HALT events, signal generation (HotScanner, RotationScanner, CUSUM thresholds, QuoteImbalance decay constants) is rewritten and Crucible rerun. Infrastructure (Phases 8-22) is NOT rebuilt.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ (all passing) |
| **8** | Pre-Conditions + P0 (SC-01→SC-18-W + v23 amendments) | **49.5h** | **NEXT** | Unit tests per SC item |
| **11** | 5-Mode Clock + SubscriptionManager (AcqRel, no SeqCst, no reqOpenOrders) | **22h** | NOT STARTED | AT-01→20 |
| **12** | Smart Router + Intraday cache + Staleness guard + Zero-spread guard | **21.5h** | NOT STARTED | AT-19→41 |
| **13** | HotScanner + RotationScanner (Dynamic σ_0 + ATR σ_noise + neutral resume) | **25.5h** | NOT STARTED | AT-41→61 |
| **14** | Infinite Chandelier (TWAP cancel on exit) + Executioner V2 | **22h** | NOT STARTED | AT-61→75 |
| **15** | RiskGate 31 Vetoes + CVaR (Maillard + EVT POT + ξ bounds + ≥50 threshold) | **21h** | NOT STARTED | AT-76→100 |
| **16** | Ouroboros (Settlement lag T+2 + ts_prior_sigma_0 + generated_at + threading fix) | **28h** | NOT STARTED | AT-98→122 |
| **17** | Telemetry (async Redis, HALT auth, thread-based asyncio fix) | **15h** | NOT STARTED | AT-119→130 |
| **18** | European Equities + UK ISIN stamp duty + Polygon retry | **21h** | NOT STARTED | AT-134→153 (+5 paper days) |
| **19** | Asia-Pac MODE A + JPY precision + reconnect delay | **21h** | NOT STARTED | AT-158→173 |
| **20** | Carry State Machine (HashSet + PnL staleness + UnauthorizedPnLStream alert) | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence | **13h** | NOT STARTED | AT-204→215 (+5 paper days) |
| **22** | Institutional Hardening (CRC32 sentinel + WAL replay timeout + S3 backup) | **34.5h** | NOT STARTED | AT-216→231 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~354h** | | **~248 acceptance tests** |

*(+9h vs v22: v23-FIX-11 watchdog +1.5h, v23-FIX-3 EVT bounds +1h, v23-FIX-4 settlement lag +1h, v23-FIX-10 threading fix +0.5h, v23-FIX-9 dynamic σ_0 +0.5h, v23-FIX-6 staleness guard +0.5h, v23-FIX-7/13 WAL fixes +1.5h, ordering/guard clarifications +2.5h)*

**At 20h/week**: ~17.7 weeks to live capital
**At 40h/week**: ~8.9 weeks to live capital

---

### Drawdown Tier Reference

| Tier | Kelly Sizing | New Entries | Existing Positions | Trigger |
|------|-------------|-------------|-------------------|---------|
| NORMAL | `kelly_scale × 100%` | Allowed | Managed normally | Default |
| **YELLOW** | `kelly_scale × 50%` | Blocked | Managed normally (exits still fire) | Ouroboros failure; drawdown −3% |
| **ORANGE** | 0% | Blocked | Close all positions at market | Drawdown −5%; WAL replay timeout; stale WAL fast-path failure |
| **RED** | 0% | Blocked | Full halt (no exits, no orders) | Drawdown −8%; manual RESUME only |

*`kelly_scale = max(0.1, min(1.0, validated_trades / 250))` — ramps from 0.1× at 0 trades to 1.0× at 250 trades.*

---

### New Files Created in Phases 8-23
*(v22 list with v23 amendments)*

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — AcqRel/Acquire AtomicUsize + Semaphore + SemaphorePermitGuard (forget+add_permits)
├── watchdog.rs                (Phase 8) — std::thread watchdog, AtomicU64 last_tick_ts, exit(1) on 120s stale [NEW v23]
├── mode_controller.rs         (Phase 11) — chrono-tz, no reqOpenOrders
├── smart_router.rs            (Phase 12) — intraday_spread_cache + generated_at staleness guard + zero-spread guard
├── isa_gate.rs                (Phase 12) — April 6 boundary
├── hot_scanner.rs             (Phase 13) — Volume-weighted OFI + zero-volume neutral 0.5 + neutral QI resume
├── rotation_scanner.rs        (Phase 13) — Gaussian-Gaussian TS + ATR σ_noise + dynamic σ_0 per asset [v23]
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — TWAP cancel on Chandelier hit
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — Maillard + EVT POT GPD + ξ bounds [-0.5,0.5] + ξ=0 exponential + ≥50 threshold [v23]
├── overnight_carry.rs         (Phase 20) — HashSet<conid> whitelist + UnauthorizedPnLStream alert
├── currency.rs                (Phase 18)
├── exchange_profile.rs        (Phase 18)
├── transaction_tax.rs         (Phase 18)
├── sub_universe_allocator.rs  (Phase 18)
└── asian_exchange.rs          (Phase 19)

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — sequential steps + EXCHANGE_TIMEZONE_MAP + settlement_lag_days + thread-based asyncio restart [v23]
├── ouroboros/symbology_mapper.py  (Phase 8)
├── telegram_reporter.py       (Phase 17)
├── pdf_generator.py           (Phase 17)
├── shadow_book.py             (Phase 17)
├── cross_timezone.py          (Phase 21)
└── asia_universe.py           (Phase 21)

config/
├── european_exchange_profiles.toml  (Phase 18)
├── european_routing_table.toml      (Phase 18)
├── transaction_tax.toml             (Phase 18)
├── asian_exchange_profiles.toml     (Phase 19)
└── asian_routing_table.toml         (Phase 19)

calibration/
├── weights.json               (Ouroboros step 10)
├── asia_cross_tz.json         (Ouroboros step 9)
├── corp_action_blocklist.json (Ouroboros step 2 — veto_date = ex_date_local - 2 biz days per exchange) [v23]
├── intraday_spread_cache.json (Ouroboros step 3 — 5-day median intraday spread + generated_at timestamp) [v23]
├── asset_volatility.json      (Ouroboros step 8 — atr_14_pct + atr_14_abs + ts_prior_sigma_0) [v23]
├── exchange_times.json        (Ouroboros step 1 — dynamic DST)
├── active_state.wal           (Ouroboros step 10 — atomic: tmp+CRC32{__aegis_crc32__}+rename) [v23]
└── compaction_manifest.json   (Phase 22 — includes last_compaction_ts for stale-WAL guard) [v23]

logs/
└── watchdog.log               (Phase 8 — std::fs, independent of WAL writer, watchdog thread only) [NEW v23]
```

---

## TDD MANDATE (NON-NEGOTIABLE)

*(unchanged from v22)*

**Rule**: For every SC item:
1. Write the test first (failing)
2. Write the implementation
3. Run `cargo test` — must pass before touching the next SC item
4. No skipping, no batching, no "I'll test it all at the end"

**Gate enforcement**: Phase 8 gate MUST contain actual `cargo test` output. Fabricated output = automatic rejection.

---

## TERMINAL KICKOFF PROMPT (Phase 8)

Paste this into a new Claude Code terminal session to begin Phase 8 implementation:

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v23.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v23.md

IMPLEMENTATION TOOLING MANDATE: Use accept-edits mode ONLY. Do NOT use bypass-permissions.
The Ralph Wiggum stop hook is retained for automated retry. All bash commands require
manual approval. This is non-negotiable per v22-FIX-6.

TDD MANDATE: For each SC item — write the test first (failing), implement, run cargo test
(passing), THEN move to the next. Never batch tests. Never advance without a green test.
This is non-negotiable.

Implement all 18 SC items in order. Write unit tests for each. Run cargo test after each
SC item before proceeding to the next.

SC-01: SIGTERM handler in main.rs.
USE tokio::signal::ctrl_c() AND tokio::signal::unix::signal(SignalKind::terminate()).
DO NOT use the ctrlc crate — it races with the tokio runtime (G2-IN12).
Handler: flatten positions → wait 30s for fills → write SystemShutdown WAL event → exit.

SC-01a: docker-compose.yml — add `stop_grace_period: 60s` to the aegis-v2 service definition

SC-02: SubscriptionManager skeleton in subscription_manager.rs.
MEMORY ORDERING: Use Ordering::AcqRel for fetch_add/fetch_sub, Ordering::Acquire for loads.
DO NOT use Ordering::SeqCst — Semaphore(100) is the budget authority; AtomicUsize is
telemetry only. Add code comment confirming this.
BUDGET: tokio::sync::Semaphore(100) for the ≤100 line budget.
PERMIT GUARD: SemaphorePermitGuard stores OwnedSemaphorePermit (via acquire_owned()).
  Drop impl: std::mem::forget(self.permit.take().unwrap()) then self.semaphore.add_permits(1).
  This bypasses tokio's built-in return and ensures exactly ONE add_permits call.
  DO NOT call add_permits AND let the permit drop naturally — that returns permits twice.
Unit test AT-18b: 1000 concurrent subscribe/cancel sequences → active_line_count never > 100.
Unit test AT-18c (ASYNC panic): tokio::spawn 100 tasks each acquiring SemaphorePermitGuard
  then calling panic!(). Join all JoinErrors. Verify semaphore.available_permits() == 100.
  (v23-FIX-1 + v23-FIX-5 + v23-FIX-12)

SC-03: LineBudget struct {carry: usize, active: usize, scan: usize} with hard
assert!(carry + active + scan <= 100)

SC-04: Two-tier data architecture: (a) ibkr_broker.rs token bucket 60 req/10min, max 6
concurrent, exponential backoff on Error 162; (b) ouroboros/data_fetch.py uses Polygon.io
for nightly 5000+ tickers; (c) separate Python token bucket for Ouroboros

SC-05: MINIMUM_ENTRY_GBP: f64 = 1500.0 — pre-entry gate in risk_arbiter.rs. SUSPENDED when
validated_trades_count < 250. Gate re-activates automatically at trade 250.

SC-06: Dust guard — FILLED portion < £500.0 → submit Peg-to-Mid limit order at
(bid+ask)/2, TIF=3min; if not filled after 3min → submit market-sell; cancel unfilled
remainder separately

SC-07: Fix V1 S3 contradiction — remove reactivation comment from mean_reversion.py

SC-08: APScheduler timezone audit in main.py — verify all pre-LSE jobs use
timezone="Europe/London"

SC-09: crossbeam-channel bounded ring buffer (capacity=50000). On TrySendError::Full →
DUAL PATH:
  (a) OFI path: VOLUME-WEIGHTED AGGREGATOR (NOT suspension). Accumulate bid_vol_sum +=
  tick.bid_vol and ask_vol_sum += tick.ask_vol for each dropped tick. After buffer drains:
  ZERO-VOLUME GUARD: if bid_vol_sum == 0.0 AND ask_vol_sum == 0.0 → emit ratio = 0.5
  (neutral) to prevent short-bias. This is CRITICAL — do not emit 0.0.
  Otherwise: emit OFI ratio = (bid_vol_sum - ask_vol_sum) / (bid_vol_sum + ask_vol_sum + 1e-9)
  Feed ratio into QI EWMA. OFI NEVER paused.
  Emit WalPayload::QuoteImbalanceCompressed { ticker_id, bid_vol_sum, ask_vol_sum, dropped_count }.
  (b) Chandelier path: aggregate H/L/V into current OHLCV bar.
Unit test AT-60: inject 200 overflow ticks with known bid_vol/ask_vol → verify OFI ratio
  matches manual calculation ± 0.001.
Unit test AT-60b: inject 50 overflow ticks with bid_vol=0.0, ask_vol=0.0 → emitted ratio = 0.5.
(v23-FIX-2)

SC-10: Internal cost-basis tracker: CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }.
Nightly clear at Ouroboros step 1 + IBKR reqPositions resync.

SC-11: SubscriptionManager active_line_count: AtomicUsize (AcqRel/Acquire). Increment on
reqMktData ACK (fetch_add(1, Ordering::AcqRel)), decrement on cancelMktData ACK
(fetch_sub(1, Ordering::AcqRel)). assert!(count <= 100) before every new subscription.
DO NOT call reqOpenOrders — this causes Error 3200 ban. AtomicUsize is sole truth. (v21-FIX-2)

SC-12: symbology_mapper.py — all 6 rules including reverse split adjustment

SC-13: (a) kelly_scale = max(0.1, min(1.0, validated_trades / 250.0)); (b) POLARS_MAX_THREADS=2
in docker-compose.yml; (c) SplitAdjustment WalPayload variant

SC-14: reqMarketDataType(3) — THE FIRST CALL in ibkr_broker.rs::connect() before any
subscribe_bars() or reqMktData calls

SC-15: StrategyId::HotScanner and StrategyId::RotationScanner in types/enums.rs

SC-16: shm_size: '2gb' in docker-compose.yml aegis-v2 service

SC-17: WalPayload::QuoteImbalanceCompressed variant in types/wal.rs. Wire into channel.rs
overflow path (SC-09a).

SC-18-W: Watchdog thread — NEW in v23 (v23-FIX-11):
  Create watchdog.rs module. Declare at module root:
    static LAST_TICK_TS: AtomicU64 = AtomicU64::new(0);
    pub fn record_tick() { LAST_TICK_TS.store(unix_ts_secs(), Ordering::Relaxed); }
  In engine.rs: call record_tick() on every received market data tick.
  In main.rs: std::thread::spawn(|| loop {
    std::thread::sleep(Duration::from_secs(60));
    let last = LAST_TICK_TS.load(Ordering::Relaxed);
    let now = unix_ts_secs();
    if is_market_hours() && (now - last) > 120 {
      eprintln!("[WATCHDOG] No tick in {}s during market hours — DEADLOCK. Exiting.", now - last);
      // Write to watchdog.log via std::fs (NOT WAL writer — may be deadlocked)
      let _ = std::fs::write("/app/logs/watchdog.log", format!("WATCHDOG TRIP at {}\n", now));
      std::process::exit(1);
    }
  });
  The watchdog thread uses NO tokio primitives. It is a pure std::thread.
  Docker restart: unless-stopped policy in docker-compose.yml.

After all 18 items have passing tests:
- Run cargo test (all tests must pass — paste literal output)
- Run docker build (must succeed)
- Verify docker-compose.yml has ALL THREE: stop_grace_period: 60s, POLARS_MAX_THREADS=2,
  shm_size: '2gb'
- Run `docker exec aegis-v2 df -h /dev/shm` → shows ≥2GB
- Verify grep on subscription_manager.rs: NO RwLock, NO reqOpenOrders, NO SeqCst
- Verify grep: NO ctrlc crate in main.rs (tokio::signal only)
- Verify SemaphorePermitGuard async panic test passes (available_permits == 100 after 100 spawns)
- Verify OFI zero-volume test passes (AT-60b: ratio==0.5 on zero-vol overflow)
- Verify watchdog thread spawns (check /proc or ps output for watchdog thread)
- Run 30-minute paper session; verify watchdog.log does NOT trip during normal operation

Do NOT start Phase 11 until Phase 8 gate is fully signed off with pasted cargo test output.
```

---

*AEGIS_MASTER_PLAN_v23.md — Generated 2026-03-09*
*Supersedes: AEGIS_MASTER_PLAN_v22.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v22.md (Claude G4 independent adversarial audit — second-order consequence analysis)*
*13 v23 fixes: G4-P1 through G4-P10 (priority) + G4-S1 through G4-S3 (structural)*
*Total acceptance tests: ~248 (vs ~235 in v22)*
*Total remaining hours: ~354h (vs ~345h in v22, +9h for v23 additions)*
