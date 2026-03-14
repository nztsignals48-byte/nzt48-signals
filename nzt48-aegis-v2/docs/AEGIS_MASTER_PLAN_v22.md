# AEGIS V2 — MASTER PLAN v22
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 22.0 | **Date**: 2026-03-09 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v21. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v21.md — the Gemini "Institutional Syndicate" G3 adversarial audit (200 bullets). New fixes are marked **[v22-FIX-N]** for traceability. There are 10 v22 priority fixes (G3-P1 through G3-P10) plus 1 critical safety fix (G3-CRITICAL-SAFETY). The v22 triage found 38 of 200 bullets were duplicates of v19/v20/v21 items already fixed; 30 were academic noise; 11 were genuine new findings — all accepted.

---

## v22 DELTA — TOP 10 GEMINI G3 PRIORITY FIXES

| Fix | Gemini ID | Trap | What was wrong in v21 | What v22 does |
|-----|-----------|------|-----------------------|---------------|
| **v22-FIX-1** | G3-P1 | RwLock writer starvation | SC-02 (v21-FIX-1) replaced Mutex with RwLock for `active_line_count`. Under continuous market-open reader load, Tokio RwLock can starve the ACK callback writer. Subscription budget drifts above 100 undetected. | Replace RwLock with `AtomicUsize(Ordering::SeqCst)` for pure count reads/writes. `Semaphore(100)` for budget constraint unchanged. No lock at all for count reads. |
| **v22-FIX-2** | G3-P2 | EOD spread cache wrong spreads | v21-FIX-4 cached EOD (auction-time) spreads. Auction spreads are 3-5x wider than intraday. SmartRouter comparing ETP intraday spread vs direct equity EOD auction spread always chooses ETP. Phase 12 routing intelligence nullified. | Replace EOD spread cache with 5-day median **INTRADAY** spread computed from tick data in Ouroboros step 3. File renamed: `intraday_spread_cache.json`. Zero-spread guard: if spread_bps == 0.0 → route to ETP. |
| **v22-FIX-3** | G3-P3 | QI suspension at peak alpha | v21-FIX-6 suspended QuoteImbalance EWMA on overflow. Overflow occurs at market open — precisely peak alpha time. HotScanner is blind when signal is richest. | Replace suspension with **volume-weighted bid/ask aggregator**: OFI = (Σbid_vol − Σask_vol) / (Σbid_vol + Σask_vol) during overflow window. OFI remains live with compressed attribution. H/L/V Chandelier path unchanged. |
| **v22-FIX-4** | G3-P4 | active_state.wal non-atomic write | v21-FIX-9 specified nightly rewrite but not write atomicity. Crash mid-write → corrupted .wal → engine fast-paths from garbage position state on restart. | Write to `active_state.wal.tmp` → CRC32 validate full file → atomic `os::rename` to `active_state.wal`. Old .wal only deleted after successful rename. CRC32 verify on load; mismatch → `ActiveStateCorrupt` log → WAL replay fallback. |
| **v22-FIX-5** | G3-P5 | Semaphore permit leak on panic | `Semaphore::acquire()` permit not returned on task panic. Over 24h, all 100 permits bleed to zero. Subscription budget exhausted silently. | Implement `SemaphorePermitGuard(Arc<Semaphore>)` with `Drop::drop()` → `Semaphore::add_permits(1)`. Used everywhere permits are acquired. Unit test: acquire → panic → verify available_permits() == 100. |
| **v22-FIX-6** | G3-P6 | bypass-permissions LLM root access | AEGIS_IMPLEMENTATION_PLAN uses `bypass-permissions` with Ralph Wiggum stop hook. This grants the coding agent unrestricted bash execution — operational suicide. | **AEGIS_IMPLEMENTATION_PLAN_v22.md: `accept-edits` ONLY.** No `bypass-permissions`. Ralph Wiggum continues for stop-hook retry logic. All bash commands require manual user approval. |
| **v22-FIX-7** | G3-P7 | Corp action timezone per-exchange | v21-FIX-8 normalized all Polygon corp action dates to Europe/London. Correct for LSE/XETRA. Wrong for TSE (JST), KRX (KST), ASX (AEST). TSE ex-date midnight JST = previous evening in London → one-day shift in blocklist. | Per-exchange timezone mapping: `EXCHANGE_TIMEZONE_MAP = {"TSE": "Asia/Tokyo", "KRX": "Asia/Seoul", "ASX": "Australia/Sydney", "LSE": "Europe/London", "XETRA": "Europe/Berlin", ...}`. Ex-date normalized to exchange local midnight, then used for LSE trading veto logic. |
| **v22-FIX-8** | G3-P8 | CarryMonitor silent discard hides bugs | v21-FIX-10 specified silent discard of unauthorized PnL updates. Silent discard hides routing bugs where AEGIS positions appear under wrong conids. First unknown conid occurrence is operationally significant. | Add Telegram `UnauthorizedPnLStream` alert on **FIRST** occurrence per conid. Subsequent occurrences of same conid: silent discard continues. Log to WAL with `WalPayload::UnauthorizedPnLStream { conid, first_seen_ts }`. |
| **v22-FIX-9** | G3-P9 | Gaussian CVaR fallback understates flash crash tail | v21-FIX-3 (Maillard K>S²-1 check) correctly gates CF expansion but falls back to Gaussian CVaR. In flash crash regime (K <= S²-1), Gaussian also has thin tails — dramatically understates actual risk. | If K <= S²-1 AND ≥20 exceedances above 95th percentile threshold `u` in last 60 returns: use **EVT Peak-Over-Threshold Generalized Pareto Distribution** (GPD) fit. Fallback to Gaussian only if fewer than 20 exceedances. |
| **v22-FIX-10** | G3-P10 | σ_noise 30-day lag punishes breakouts | v21-FIX-7 made σ_noise dynamic from 30-day rolling stddev. Correct vs static 0.03 but lags breakout regimes by ~15 days. Thompson Sampler systematically starves assets during volatility expansion phase. | Use real-time ATR percentile: `σ_noise = max(0.02, atr_14_pct × 1.5)` where `atr_14_pct` is 14-period ATR as % of mid-price. Updated on each Ouroboros tick data load. Responds to volatility expansion within current session. |

**v22-CRITICAL-SAFETY**: G3-P6 bypass-permissions → accept-edits. Addresses operational safety risk. AEGIS_IMPLEMENTATION_PLAN_v22.md is a separate document to be created; this plan notes the mandate.

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

**V1 Critical Bugs (unchanged from v21):**

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

### 2.1 Combined P0 + P1 Matrix (v19 + v20 + v21 + v22 fixes)

**P0 — Fatal (System Will Not Function):**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-1 | Docker SIGKILL at 10s vs 30s SIGTERM wait | `stop_grace_period: 60s` in docker-compose.yml | **v20-FIX-1, Phase 8** |
| P0-2 | Polars vCPU starvation → IBKR disconnect | `POLARS_MAX_THREADS=2` in docker-compose.yml | **v20-FIX-2, Phase 8** |
| P0-3 | Half-Kelly + Min Entry = 0 trades possible | Dynamic Kelly ramp: floor 0.1× at 0 trades | **v20-FIX-3, Phase 8/15** |
| P0-4 | WAL compaction severs open position history | Exclude open position events + nightly active_state.wal rewrite | **v20-FIX-4 + v21-FIX-9, Phase 22** |
| P0-5 | reqPnL 1-per-connection IBKR limit | Account-level reqPnL instead of reqPnLSingle | **v20-FIX-5, Phase 20** |
| P0-6 | clock.rs BST addition missing % 86400 | chrono-tz Europe::London | **v20-FIX-6, Phase 11** |
| P0-7 | RwLock writer starvation on active_line_count | **AtomicUsize(Ordering::SeqCst) + Semaphore(100)** — no lock for counting | **v22-FIX-1 (upgrades v21-FIX-1), Phase 8** |
| P0-8 | No reqMarketDataType(3) call in broker | Add as first call in ibkr_broker.rs::connect() | **v20-FIX-8, Phase 8** |
| P0-9 | Heartbeat only fires in DARK (22h gap) | Engine-side 30-min heartbeat Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent from WAL | Add HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |
| **P0-11** | **reqOpenOrders wrong API — Error 3200 ban** | **Remove reconciliation; use internal AtomicUsize only** | **v21-FIX-2, Phase 11** |
| **P0-12** | **Docker /dev/shm 64MB → Polars Bus error** | **shm_size: '2gb' in docker-compose.yml** | **v21-FIX-5, Phase 8** |
| **P0-13** | **bypass-permissions grants LLM root execution** | **accept-edits ONLY in AEGIS_IMPLEMENTATION_PLAN_v22.md** | **v22-FIX-6, Process** |

**P1 — High (System Will Fail in Common Conditions):**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | EOD auction spread cache routes SmartRouter to ETP always | 5-day median **intraday** spread in Ouroboros step 3; renamed intraday_spread_cache.json | **v22-FIX-2 (upgrades v21-FIX-4), Phase 12** |
| P1-2 | Telegram polling thread dies silently | Infinite retry loop with exponential backoff | Phase 17 |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker cache invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV allocation | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | QI suspension at market open loses peak alpha signal | Volume-weighted bid/ask aggregator; OFI live during overflow | **v22-FIX-3 (upgrades v21-FIX-6), Phase 8** |
| P1-6 | σ_noise 30-day lag punishes breakout ETPs | ATR percentile: max(0.02, atr_14_pct × 1.5) | **v22-FIX-10 (upgrades v21-FIX-7), Phase 13** |
| P1-7 | Corp action ex-date timezone wrong for non-London exchanges | EXCHANGE_TIMEZONE_MAP per exchange | **v22-FIX-7 (upgrades v21-FIX-8), Phase 16** |
| P1-8 | WAL compaction unbounded file for mega-runners | Nightly active_state.wal rewrite (atomic) | **v21-FIX-9 + v22-FIX-4, Phase 22** |
| P1-9 | reqPnL parses manual holdings → carry loop crash | HashSet<conid> whitelist in CarryMonitor | **v21-FIX-10, Phase 20** |
| P1-10 | Cornish-Fisher domain violation during flash crash | Maillard K>S²-1 check; EVT POT GPD fallback | **v21-FIX-3 + v22-FIX-9, Phase 15** |
| P1-11 | Cost basis wrong after overnight split | Nightly clear + IBKR reqPositions resync | Phase 8 |
| P1-12 | Dust market-sell slippage on illiquid | Peg-to-Mid limit, 3min TIF | Phase 8 |
| P1-13 | AtomicUsize leaks on dropped ACK | Internal tracking only; no reqOpenOrders | **v21-FIX-2, Phase 11** |
| P1-14 | FTT intraday exemption lost on carry | Flag FTT entries as no-carry eligible | Phase 18/20 |
| P1-15 | NZX misses opening auction daily | Pre-subscribe NZX at 22:55 UTC in DARK | Phase 19 |
| P1-16 | ISA tax year Jan 1 vs April 6 | Fix isa_gate.rs boundary to April 6 | Phase 12 |
| P1-17 | HKEX board lot → 0-share order | Fallback to ETP when lot×price > Kelly | Phase 12 |
| P1-18 | Polars parallel step execution → OOM | Enforce sequential step execution | Phase 16 |
| P1-19 | Carry allocator wrong — assumes 3 not 6 | Dynamic: available = 100 − (carry_count × 2) | **v20-FIX-14, Phase 20** |
| P1-20 | Semaphore permit leak on task panic | SemaphorePermitGuard with Drop::drop() | **v22-FIX-5, Phase 8** |
| P1-21 | active_state.wal non-atomic write → corrupt state on restart | tmp + CRC32 + atomic rename pattern | **v22-FIX-4, Phase 22** |

### 2.2 Binding Architectural Mandates (v19 + v20 + v21 + v22 additions)

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Use Polars `LazyFrame` + Arrow zero-copy. 500-ticker batches. RSS ceiling 3.5GB. | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** `crossbeam-channel` bounded (capacity=50,000). Overflow → **volume-weighted bid/ask aggregator for OFI** (OFI remains live, ratio preserved). Separately: aggregate H/L/V into current bar for Chandelier (price path). **(v22-FIX-3 supersedes v21-FIX-6)** | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox fix.** IBKR `reqHistoricalData` token bucket (60 req/10min) for active ~100 tickers ONLY. Nightly 5,000+ ticker universe → Polygon.io/Databento. | Phase 8 + 16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed ONLY when live position exists. HotScanner/RotationScanner candidates do NOT get underlyings tracked. | Phase 11 |
| **GEM-A5** | **Drawdown tier nomenclature.** Yellow = Kelly × dynamic_ramp, no new entries. Orange = close all positions. Red = full halt. Ouroboros failure → Yellow. | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time calculations via `chrono_tz::Europe::London`, not manual approximation. | Phase 11 |
| **v20-A2** | **AtomicUsize(Ordering::SeqCst) for active_line_count + Semaphore(100) for budget.** No RwLock for counting. No lock held across .await network calls. SemaphorePermitGuard Drop guarantee. **(v22-FIX-1 supersedes v21-FIX-1; v22-FIX-5 adds SemaphorePermitGuard)** | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler with ATR-percentile σ_noise.** Continuous PnL% reward, not binary win/loss. σ_noise = max(0.02, atr_14_pct × 1.5) per asset. **(v22-FIX-10 supersedes v21-FIX-7)** | Phase 13 |
| **v20-A4** | **Account-level reqPnL only + CarryMonitor whitelist + UnauthorizedPnLStream alert.** Never use `reqPnLSingle`. HashSet<conid> whitelist. First-occurrence Telegram alert per unknown conid. **(v22-FIX-8 extends v21-FIX-10)** | Phase 20 |
| **v21-A1** | **No reqOpenOrders for line reconciliation.** Internal AtomicUsize tracking only. reqOpenOrders is an execution API, not a data subscription API. **(v21-FIX-2)** | Phase 11 |
| **v21-A2** | **shm_size: '2gb' in docker-compose.yml.** Polars mmap requires ≥2GB /dev/shm. Default 64MB causes Bus error on 5,000-ticker scan. **(v21-FIX-5)** | Phase 8 |
| **v21-A3** | **Maillard (2012) CF domain check + EVT POT fallback.** K > S²-1 check required before CF expansion. If K <= S²-1 AND ≥20 tail exceedances → GPD fit. If <20 exceedances → Gaussian CVaR. **(v22-FIX-9 extends v21-FIX-3)** | Phase 15 |
| **v21-A4** | **Volume-weighted OFI aggregator on overflow.** OFI remains live: ratio = (Σbid_vol − Σask_vol)/(Σbid_vol + Σask_vol). Chandelier H/L/V path aggregates independently. **(v22-FIX-3 supersedes v21-A4 suspension)** | Phase 8 |
| **v21-A5** | **active_state.wal nightly rewrite — atomic.** Write to .tmp → CRC32 validate → os::rename. CRC32 verify on load. Mismatch → WAL replay fallback. **(v22-FIX-4 extends v21-FIX-9)** | Phase 22 |
| **v22-A1** | **EXCHANGE_TIMEZONE_MAP for corp action ex-dates.** Per-exchange local midnight normalization before trading veto logic. TSE→Asia/Tokyo, KRX→Asia/Seoul, ASX→Australia/Sydney, LSE→Europe/London. **(v22-FIX-7)** | Phase 16 |
| **v22-A2** | **intraday_spread_cache.json (5-day median intraday spread).** Not EOD auction spread. SmartRouter routing uses intraday liquidity conditions. Zero-spread guard → ETP fallback. **(v22-FIX-2)** | Phase 12/16 |
| **v22-A3** | **accept-edits ONLY in implementation tooling.** No bypass-permissions. Ralph Wiggum stop hook retained. Bash commands require manual approval. **(v22-FIX-6)** | Process |

### 2.3 Deferred (Post-Crucible)

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

---

## PART 3 — PHASE PLAN

### Numbering Convention

- **Phases 1-7**: COMPLETE (V2 Rust core)
- **Phase 8**: Pre-conditions and P0 hardening (NEXT) — **17 SC items + v22 amendments**
- **Phases 9-10**: Reserved for future use
- **Phases 11-23**: Granular build phases

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 48h | **Status**: NEXT — must complete before Phase 11
*(+4h vs v21 for v22-FIX-1 AtomicUsize+SemaphorePermitGuard, v22-FIX-3 volume-weighted aggregator, additional tests)*

**Rationale**: Foundation hardening. 17 SC items from v21 retained. v22 amendments: SC-02 upgraded from RwLock to AtomicUsize (v22-FIX-1); SC-09 upgraded from QI suspension to volume-weighted aggregator (v22-FIX-3); SemaphorePermitGuard added (v22-FIX-5).

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: flatten → 30s fill wait → WAL shutdown event → exit | main.rs | — |
| **SC-01a** | `stop_grace_period: 60s` added to docker-compose.yml **(v20-FIX-1)** | docker-compose.yml | v20-FIX-1 |
| **SC-02** | SubscriptionManager skeleton: **`AtomicUsize(Ordering::SeqCst)`** for `active_line_count` (NOT RwLock, NOT Mutex). **`tokio::sync::Semaphore(100)`** for the ≤100 budget constraint. **`SemaphorePermitGuard(Arc<Semaphore>)`** with `Drop::drop()` → `add_permits(1)` to prevent leak on panic. No lock held across .await. ACK via AtomicUsize only. **(v22-FIX-1 + v22-FIX-5 — supersedes v21-FIX-1)** | subscription_manager.rs | v22-FIX-1, v22-FIX-5 |
| SC-03 | LineBudget struct `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket (60 req/10min, 6 concurrent, Error 162 backoff) for active ~100 tickers; Polygon.io/Databento for nightly 5,000+ universe; single Rust token bucket, separate Python Ouroboros bucket | ibkr_broker.rs + ouroboros/data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` pre-entry gate in risk_arbiter.rs — **suspended during dynamic Kelly ramp below 250 trades** **(v20-FIX-3)** | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard: if `filled_gbp < 500.0` → submit Peg-to-Mid limit order at mid-price, TIF=3min; if not filled in 3min → submit market-sell; cancel unfilled remainder separately | exit_engine.rs | v19-FIX-1 |
| SC-07 | Fix V1 S3 contradiction: remove conflicting reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler timezone audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| **SC-09** | `crossbeam-channel` bounded ring buffer (capacity=50,000). On `TrySendError::Full` → **dual handling — v22 AMENDMENT:** (a) OFI path: **volume-weighted aggregator** — accumulate `bid_vol_sum` and `ask_vol_sum`; emit synthetic OFI ratio = (bid_vol_sum − ask_vol_sum) / (bid_vol_sum + ask_vol_sum + ε); OFI EWMA remains live **(v22-FIX-3 — supersedes v21-FIX-6 QI suspension)**; (b) Chandelier path: aggregate H/L/V into current bar (bar.high=max, bar.low=min, bar.volume+=) to preserve price extremes **(v20-FIX-13 retained)**. Emit `QuoteImbalanceCompressed { ticker_id, bid_vol_sum, ask_vol_sum, dropped_count }` WAL event. Increment `overflow_counter`. | python_bridge.rs + channel.rs + types/wal.rs | GEM-A2 + v22-FIX-3 |
| SC-10 | Internal cost-basis tracker: `HashMap<TickerId, CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }>`. VWAP cost basis. Nightly clear + IBKR reqPositions resync at Ouroboros step 1. | portfolio.rs | G-09 |
| SC-11 | SubscriptionManager `active_line_count: AtomicUsize`; increment on `reqMktData` ACK, decrement on `cancelMktData` ACK; `assert!(count <= 100)` before every new subscription. **No reqOpenOrders reconciliation — internal tracking only. (v21-FIX-2)** | subscription_manager.rs | v21-FIX-2 |
| SC-12 | `symbology_mapper.py`: rules: (a) space→dot; (b) LSE suffix→prefix; (c) exchange pass-through; (d) preferred shares `BAC PR D → BAC/PD`; (e) reverse mapping `to_ibkr(polygon_symbol)` | ouroboros/symbology_mapper.py | v19-FIX-2 |
| **SC-13** | Dynamic Kelly ramp **(v20-FIX-3):** `kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))`. Add `POLARS_MAX_THREADS=2` to docker-compose.yml env. `SplitAdjustment` WAL event added. | risk_arbiter.rs + docker-compose.yml + types/wal.rs | v20-FIX-3 |
| **SC-14** | `reqMarketDataType(3)` first call **(v20-FIX-8)** | ibkr_broker.rs | v20-FIX-8 |
| **SC-15** | StrategyId enum extension **(v20-FIX-10):** Add `StrategyId::HotScanner` and `StrategyId::RotationScanner` to `types/enums.rs`. | types/enums.rs + types/wal.rs | v20-FIX-10 |
| **SC-16** | **`shm_size: '2gb'` in docker-compose.yml (v21-FIX-5):** Add to the `aegis-v2` service definition. Verify: `docker exec aegis-v2 df -h /dev/shm` shows ≥2GB. | docker-compose.yml | v21-FIX-5 |
| **SC-17** | **`WalPayload::QuoteImbalanceCompressed` variant (v22-FIX-3):** Add WAL event type with fields `ticker_id: TickerId, bid_vol_sum: f64, ask_vol_sum: f64, dropped_count: u32`. Wire into channel.rs overflow path. Note: renamed from QuoteImbalanceInvalidated — OFI is no longer suspended, it is compressed. | types/wal.rs | v22-FIX-3 |

**Gate**: All 17 items coded + unit tested; `cargo test` passes; `docker build` passes; crossbeam dual-path overflow verified (OFI volume-weighted ratio preserved, H/L/V preserved); SemaphorePermitGuard Drop verified (panic test: available_permits == 100 after catch_unwind); AtomicUsize no-lock verification (no RwLock in subscription_manager.rs grep); symbology mapper round-trip tested; dynamic Kelly ramp produces valid orders from day 1; `reqMarketDataType(3)` verified as first IBKR call; docker-compose.yml has `stop_grace_period: 60s`, `POLARS_MAX_THREADS=2`, `shm_size: '2gb'`; `df -h /dev/shm` shows ≥2GB inside container

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 22h | **Depends on**: Phase 8
*(unchanged from v21 except: AtomicUsize upgrade from v22-FIX-1 propagated)*

**v22 Amendment:** SC-02 upgrade (v22-FIX-1) changes `RwLock` to `AtomicUsize(Ordering::SeqCst)` for `active_line_count`. All Phase 11 subscription_manager.rs code must use AtomicUsize, not RwLock. The Semaphore(100) budget constraint is unchanged. The `reqOpenOrders` removal (v21-FIX-2) is unchanged.

**Deliverables:**

- `clock.rs` REWRITTEN — chrono-tz **(v20-FIX-6):**
  - `use chrono_tz::Europe::London;`
  - `fn now_london() -> DateTime<London>` — authoritative London local time
  - `fn from_utc_secs(s: u32) -> TradingMode` — chrono-tz conversion, no manual approximation
  - `TradingMode` enum: `{ModeA, ModeB, ModeBPlus, ModeC, Dark}`
  - `mode_b_plus_end_utc(date: NaiveDate) -> u32` using chrono-tz for DST-correct LSE close
  - Cargo.toml: add `chrono-tz = "0.9"` dependency

- `subscription_manager.rs` (NEW, extends SC-02/SC-03/SC-11/SC-16/SC-17 skeleton):
  - Full **`AtomicUsize(Ordering::SeqCst)`** for `active_line_count` **(v22-FIX-1 — no RwLock)**
  - `tokio::sync::Semaphore(100)` for the ≤100 line budget constraint
  - `SemaphorePermitGuard(Arc<Semaphore>)` wrapping all permit acquisitions **(v22-FIX-5)**
  - Deterministic: `cancel → wait for cancelMktData ACK → subscribe`
  - ACK: `active_line_count.fetch_sub(1, Ordering::SeqCst)` on cancelMktData ACK
  - **No reqOpenOrders reconciliation (v21-FIX-2):** AtomicUsize is sole truth. WAL replay on restart reconstructs count.
  - Timeout-based ACK: if no ACK within 2s → log timeout → proceed + schedule WAL `LineBudgetUncertain` event
  - IBKR bar→tick migration: Phase 11 MUST migrate `subscribe_bars()` → `reqMktData` tick-by-tick
  - Proptest: 500 random subscribe/cancel sequences, invariant holds
  - **Scanner Conservation Rule (GEM-A4):** HotScanner/RotationScanner candidates NEVER trigger underlying subscription

- `mode_controller.rs` (NEW):
  - State machine driving mode transitions
  - `ModeChange { from, to, utc_ts }` events to engine
  - Event channel bounded capacity=16
  - `ModeTransitionBlind` WAL event during transition

- **NZX pre-subscribe at 22:55 UTC:** SubscriptionManager reserves NZX lines during DARK

**Acceptance Tests (AT-01 to AT-18):**
- AT-01 through AT-16: same as v21
- AT-17: Leap year — Feb 29 2028 — chrono-tz gives correct mode boundary
- **AT-18: reqOpenOrders MUST NOT be called for line reconciliation. Grep confirms no reqOpenOrders call in subscription_manager.rs.**
- **AT-18b: AtomicUsize no-lock: grep subscription_manager.rs for RwLock → zero matches. Semaphore permit acquired via SemaphorePermitGuard only.**

**Gate**: 19 tests pass; chrono-tz DST flip verified; NZX pre-subscribe at 22:55 UTC verified; `active_line_count <= 100` proptest 1000 cases; **grep confirms no RwLock and no reqOpenOrders in subscription_manager.rs**

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 21h | **Depends on**: Phase 11
*(+1h vs v21 for intraday spread cache, zero-spread guard, additional test)*

**v22 Amendments:**

- **Intraday spread cache (v22-FIX-2):** SmartRouter reads `calibration/intraday_spread_cache.json` (renamed from `eod_spread_cache.json`). Contains 5-day median INTRADAY spread per ticker (not EOD auction spread). Auction spreads are 3-5x wider than intraday — using EOD spreads caused SmartRouter to always route to ETP. Zero-spread guard: if `cached_spread_bps == 0.0` → skip direct equity route, use ETP.
- **Reverse split symbology (G2-M1, v21):** `symbology_mapper.py` handles reverse splits (1-for-10): adjust `total_shares /= split_factor`, `price_basis *= split_factor`. `SplitAdjustment` WAL event with `split_type: {Forward, Reverse}` and `ratio: f64`.
- **XETRA randomized closing auction (G2-M2, v21):** XETRA unrosses randomly between 17:30:00 and 17:32:00 CET. Subscribe `reqTradingHours` for XETRA; if within `[15:20 UTC, 15:32 UTC]` → treat as auction window.

**Deliverables:**

- `smart_router.rs` (NEW):
  - ETP-first principle
  - **Intraday spread cache lookup (v22-FIX-2):** `intraday_spread_cache.json` read from calibration. 5-day median intraday spread. Zero-spread guard: `if spread_bps == 0.0 { route_to_etp(); return; }`. Real-time snapshot at 800ms timeout only for Tier 1 (ADV > £500k). Snapshot queue: max 5 concurrent.
  - Route logic: `if cached_intraday_spread_bps > 0.0 AND cached_intraday_spread_bps < etp_spread_bps × 0.9 AND health passes → direct route; else ETP`.
  - Full cost model: FX drag + FTT + IBKR commission + stamp duty
  - Integer shares: `floor(kelly_gbp / lot_price_gbp)`
  - HKEX board lot ETP fallback
  - ETP 30-day tracking error check (>5% → demote)
  - FTT market cap ±10% hysteresis

- `isa_gate.rs` (NEW):
  - Hard-blocks: Taiwan, China, India — `HashSet<&'static str>`
  - ISA tax year boundary = April 6
  - ISA annual limit check

**Acceptance Tests (AT-19 to AT-41):**
- AT-19 through AT-36: same as v21
- **AT-37: Intraday spread cache hit: illiquid ticker → no real-time snapshot, 5-day median intraday spread used for routing decision**
- **AT-37b: Zero-spread guard: cached_spread_bps == 0.0 → routes to ETP without divide; no panic**
- **AT-38: Real-time snapshot: Tier 1 ticker (ADV > £500k) → 800ms timeout, not 200ms**
- **AT-39: Reverse split: CostBasisEntry with 100 shares at £50 → 1-for-10 → 10 shares at £500; SplitAdjustment WAL event logged**
- **AT-40: XETRA auction window: time within [15:20-15:32 UTC] → AuctionAvoidance veto fires**

**Gate**: 23 tests pass; intraday spread cache verified (5-day median, not EOD); zero-spread guard verified (routes to ETP, no divide); XETRA window verified; reverse split WAL event verified

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 25h | **Depends on**: Phase 12
*(+1h vs v21 for ATR percentile σ_noise, QI neutral-state resume, updated tests)*

**v22 Amendments:**

- **ATR percentile σ_noise (v22-FIX-10):** `σ_noise = max(0.02, atr_14_pct × 1.5)` where `atr_14_pct` is the 14-period ATR as percentage of mid-price. Updated on each Ouroboros tick data load. Responds to volatility expansion in the current session rather than lagging 15 days behind. Replaces 30-day rolling stddev (v21-FIX-7).
- **QI neutral-state resume (v22-M2):** After `QuoteImbalanceCompressed` event, if overflow subsides (overflow_counter = 0 for ≥5 seconds), QI EWMA resumes from **0.5 (neutral state)** — NOT from the last EWMA value. Last EWMA value may reflect corrupted directional data from the overflow period.
- **trend_velocity normalization (G2-M15, v21):** Normalize raw `trend_velocity` by dividing by asset's 30-day stddev before RotationScanner composite score. Prevents high-beta monopoly.
- **Kalman covariance reset on gap (G2-M20, v21):** If overnight gap > 2× ATR, reset Kalman filter covariance matrix `P` to prior `P_0`.

**Deliverables:**

- `hot_scanner.rs` (NEW): QuoteImbalance EWMA, CUSUM, Kalman, meta-label gate 0.55
  - **QI resume after overflow: reset EWMA to 0.5 after overflow_counter == 0 for ≥5 consecutive seconds (v22-M2). Do not resume from last EWMA value.**
  - **Volume-weighted OFI during overflow (v22-FIX-3):** OFI input = compressed ratio from QuoteImbalanceCompressed WAL event; EWMA update proceeds with this ratio, not suspended.

- `rotation_scanner.rs` (NEW):
  - **Gaussian-Gaussian Thompson Sampler:** `σ_noise = max(0.02, atr_14_pct × 1.5)` **(v22-FIX-10)**
  - Prior: `μ_0 = 0.0`, `σ_0 = 0.05`
  - Hard slot limit: max 40 HotScanner + 10 RotationScanner = 50 total scanner lines
  - **trend_velocity normalization (v21):** `normalized_velocity = raw_velocity / asset_30day_stddev`
  - WAL attribution: `StrategyId::HotScanner` / `StrategyId::RotationScanner`

- `universe_scanner.rs` (NEW): ADV filter, RVOL calc, 100-line budget respect

**Acceptance Tests (AT-41 to AT-61):**
- AT-41 through AT-55: same as v21 (renumbered)
- **AT-56: ATR percentile σ_noise: 3x ETP with atr_14_pct=0.06 → σ_noise = max(0.02, 0.06 × 1.5) = 0.09; direct equity atr_14_pct=0.01 → σ_noise = 0.02 (floor). TS allocates more lines to ETP.**
- **AT-57: trend_velocity normalization: high-beta asset (30d stddev=5%) and low-beta (1%) with same raw velocity → equal normalized scores.**
- **AT-58: Kalman covariance reset: overnight gap > 2×ATR → P reset to P_0; filter converges within 10 ticks post-gap.**
- **AT-59: QI neutral resume: overflow subsides after 5s → EWMA reset to 0.5; NOT resumed from last value (which was 0.82 in test case).**
- **AT-60: Volume-weighted OFI during overflow: 200 ticks aggregated; OFI ratio computed from bid_vol_sum/ask_vol_sum matches manual calculation ±0.001.**

**Gate**: 20 tests pass; ATR percentile σ_noise loaded from asset_volatility.json (atr_14_pct field); Gaussian-Gaussian TS verified; QI neutral-state resume verified; volume-weighted OFI during overflow verified

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 22h | **Depends on**: Phase 13
*(unchanged from v21)*

**v21 Amendment (retained):**

- **Cancel TWAP slices on Chandelier stop hit (G2-M17):** If Chandelier trailing stop triggers during an active entry TWAP sequence, immediately cancel all remaining unfilled TWAP slices. Submit a single exit order at market. Do NOT complete the entry if the exit signal fires first.

**Deliverables:**

- `exit_engine.rs` EXTENDED — Infinite Chandelier with 8 adaptive multipliers
  - Leverage-adjusted floor: `stop_distance = max(multiplier × ATR, 1.5 × spread × leverage_factor)`
  - Ratchet enforcer: stop can ONLY increase
  - **TWAP cancel on Chandelier hit:** `executioner_v2.rs` checks `exit_engine::is_exit_pending(ticker)` before each TWAP slice; if true → cancel remaining slices → submit exit

- `executioner_v2.rs` (NEW):
  - ADV execution gate: `≤ 1% of 5-min rolling volume from current session`
  - U-shaped TWAP with current session volume
  - TWAP US half-day abort at 30 min before close
  - Alpha half-life TWAP

- `spread_veto.rs` (NEW): U-shaped intraday spread tolerance

**Acceptance Tests (AT-61 to AT-80):**
- AT-61 through AT-74: same as v21
- **AT-75: TWAP slice cancel: Chandelier stop triggers during 3rd of 5 TWAP slices → slices 4 and 5 cancelled, exit order submitted**

**Gate**: 15 tests pass; Chandelier-TWAP interaction verified; leverage-adjusted floor verified at 3x ETP

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 20h | **Depends on**: Phase 14
*(+2h vs v21 for EVT POT GPD implementation, additional test)*

**v22 Amendments:**

- **EVT POT GPD fallback (v22-FIX-9):** When CF expansion is invalid (K <= S²-1): check count of returns in last 60 that exceed threshold `u = 95th percentile`. If exceedance count ≥ 20 → fit Generalized Pareto Distribution via Maximum Likelihood on the exceedances. Use GPD CVaR formula: `CVaR_GPD = u + σ/(1-ξ) × ((n/k × α)^(-ξ) - 1) / ξ` where σ, ξ are GPD shape/scale parameters. If exceedance count < 20 → Gaussian CVaR (insufficient data for GPD fit). This replaces the v21-FIX-3 simple Gaussian fallback.
- **Maillard (2012) CF domain check (v21-FIX-3, retained):** `if N < 20 OR |S| >= 2 OR K <= S² - 1 → use EVT POT fallback chain above`.
- **CVaR limit scaling with Kelly ramp (v21, retained).**
- **VIX circuit breaker blind at startup (v21, retained).**
- **CVaR max-correlation damping factor 0.8 (v21, retained).**

**Deliverables:**

- `risk_arbiter.rs` EXTENDED — 31 vetoes
- `cvar_heat.rs` (NEW):
  - **CF expansion gated: N≥20 AND |S|<2 AND K > S²-1 (v21-FIX-3)**
  - **EVT POT GPD fallback (v22-FIX-9):** if CF invalid AND ≥20 exceedances above u=95th pct → GPD fit via MLE on exceedances; else Gaussian CVaR
  - CVaR limit scales with `kelly_scale`
  - VIX circuit breaker with startup blind spot protection
  - CVaR max-correlation damping factor 0.8
- Dynamic Kelly ramp: `kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))`

**Acceptance Tests (AT-76 to AT-98):**
- AT-76 through AT-92: same as v21
- **AT-93: Maillard K>S²-1 check: K=0.1, S=0.5 → S²-1 = -0.75; K>S²-1 → CF allowed; K=0.1, S=1.5 → S²-1 = 1.25; K NOT > S²-1 → EVT POT fallback chain**
- **AT-93b: EVT POT path: K <= S²-1, 30 exceedances above 95th pct → GPD fit applied; CVaR_GPD > Gaussian CVaR by ≥ 20% on fat-tailed test distribution**
- **AT-94: CVaR-Kelly scaling: kelly_scale=0.1 → CVaR limit = 10% of base; kelly_scale=0.5 → CVaR limit = 50% of base**
- **AT-95: VIX blind at startup: first 4 minutes of data → VixHistoryInsufficient logged; circuit breaker NOT evaluated; no false trips**

**Gate**: 22 tests pass; 31 total vetoes confirmed; Maillard domain check verified; EVT POT GPD path verified (CVaR > Gaussian on fat-tailed distribution); CVaR-Kelly scaling verified; VIX blind spot at startup handled

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 26h | **Depends on**: Phase 15
*(+2h vs v21 for intraday spread computation, EXCHANGE_TIMEZONE_MAP, atr_14_pct, asyncio fix in data_fetch.py)*

**v22 Amendments:**

- **Intraday spread cache (v22-FIX-2):** Ouroboros step 3 (universe discovery) computes 5-day median INTRADAY spread from tick data per ticker. Writes `calibration/intraday_spread_cache.json`: `{"ASML.NA": {"spread_bps": 8.2, "adv_gbp": 850000, "tier": 1}, ...}`. This replaces the v21 EOD spread cache.
- **EXCHANGE_TIMEZONE_MAP (v22-FIX-7):** Ouroboros step 2, after Polygon corp action fetch: `EXCHANGE_TIMEZONE_MAP = {"TSE": "Asia/Tokyo", "KRX": "Asia/Seoul", "ASX": "Australia/Sydney", "LSE": "Europe/London", "XETRA": "Europe/Berlin", "NYSE": "America/New_York", "NASDAQ": "America/New_York"}`. Determine ticker exchange from Polygon metadata. `ex_date_local = datetime.fromisoformat(polygon_date).astimezone(ZoneInfo(exchange_tz)).date()`. Use `ex_date_local` for `corp_action_blocklist.json`.
- **atr_14_pct in asset_volatility.json (v22-FIX-10):** Ouroboros step 8 (Thompson Sampling update) computes 14-period ATR as % of mid-price per asset from tick data loaded in step 3. Writes `calibration/asset_volatility.json`: `{"ASML.NA": {"atr_14_pct": 0.012, "atr_14_abs": 0.85}, "QQQ3.L": {"atr_14_pct": 0.067, "atr_14_abs": 12.3}, ...}`. RotationScanner reads `atr_14_pct` to compute `σ_noise`.
- **asyncio RuntimeError fix in data_fetch.py (v22-IN17):** `ouroboros/data_fetch.py` outer async loops must catch `RuntimeError: Event loop is closed` separately. On RuntimeError → `asyncio.new_event_loop()` before restarting. Same pattern as Phase 17 telegram_reporter.py fix.

**Deliverables:**

- `ouroboros/` EXTENDED — 10-step pipeline:
  1. **Data fetch** — Polygon.io + IBKR active tickers; nightly cost basis clear + reqPositions resync
  2. **Corporate action blocklist** — Polygon.io; **normalise dates via EXCHANGE_TIMEZONE_MAP to exchange-local midnight (v22-FIX-7)**; atomic write
  3. **Universe discovery** — 5,000+ tickers; compute **5-day median INTRADAY spread**; write `intraday_spread_cache.json` **(v22-FIX-2)**; Polars LazyFrame 500-ticker batches
  4. **Feature engineering** — Polars LazyFrame; write to /dev/shm during processing
  5. **Scoring** — ASER: momentum 30%, liquidity 20%, volatility 20%, regime 15%, recency 15%
  6. **Meta-label training** — Logistic Regression / LightGBM fallback
  7. **Chandelier calibration** — ATR, MAE/MFE profiling
  8. **Thompson Sampling update** — Gaussian-Gaussian posteriors; compute **atr_14_pct per asset**; write `asset_volatility.json` **(v22-FIX-10)**
  9. **DCC-GARCH update** — cross-asset correlation matrix; write `calibration/asia_cross_tz.json` with `updated_at` timestamp
  10. **PDF generation + artifact write + Telegram ALIVE** — daily summary report; active_state.wal write (v22-FIX-4 atomic pattern)

- Sequential step enforcement, Polars mandate, atomic blocklist write, Parquet cleanup — all retained from v21
- **asyncio RuntimeError safe restart in data_fetch.py (v22-IN17)**

**Acceptance Tests (AT-98 to AT-120):**
- AT-98 through AT-110: same as v21
- **AT-111: Polygon EST corp action date normalisation (LSE): '2026-04-05T04:00:00-05:00' (US EDT) → Exchange=LSE → timezone=Europe/London → normalised '2026-04-05'**
- **AT-111b: Polygon TSE corp action: '2026-04-10T00:00:00+09:00' (JST) → Exchange=TSE → timezone=Asia/Tokyo → ex_date_local='2026-04-10'; for LSE trading veto, this corresponds to '2026-04-09' London time (previous trading day in London)**
- **AT-112: Intraday spread cache: intraday_spread_cache.json present after step 3; spread_bps reflects 5-day median (not EOD); ADV and tier fields present**
- **AT-113: asset_volatility.json: present after step 8; QQQ3.L atr_14_pct > 0.05 (3x ETP); ASML.AS atr_14_pct < 0.02 (unleveraged large-cap)**

**Gate**: 23 tests pass; EXCHANGE_TIMEZONE_MAP verified with TSE JST test case; intraday spread cache verified non-empty with median computation; asset_volatility.json has atr_14_pct field; asyncio RuntimeError safe restart in data_fetch.py verified

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 15h | **Depends on**: Phase 16
*(unchanged from v21)*

**v21 Amendments (retained):**

- **Redis heartbeat async client (G2-IN2):** Async Redis client (not synchronous) for `aegis_heartbeat_ts` write in `engine.rs`.
- **Telegram bot authorization (G2-M28):** HALT command must check sender `chat_id` against `TELEGRAM_AUTHORIZED_CHAT_ID`.
- **asyncio RuntimeError fix (G2-IN6):** `telegram_reporter.py` safe restart loop.

**Deliverables:**
- Engine-side heartbeat via async Redis client
- `telegram_reporter.py`: authorized chat_id HALT check, asyncio safe restart
- All v20 deliverables retained

**Acceptance Tests (AT-119 to AT-130):**
- AT-119 through AT-129: same as v21
- **AT-130: asyncio RuntimeError in data_fetch.py: simulate closed loop → new_event_loop() created; pipeline step continues (v22-IN17 verification)**

**Gate**: 16 tests pass; async Redis heartbeat verified; HALT authorization verified; asyncio RuntimeError recovery in both telegram_reporter.py and data_fetch.py verified

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 21h | **Depends on**: Phase 17
*(unchanged from v21)*

**v21 Amendments (retained):**
- UK stamp duty on European MTFs (ISIN-based, not exchange-based)
- Ouroboros step 2 retry on Polygon 502 (max 3 attempts, exponential backoff)

**Deliverables:**
- `transaction_tax.rs` (NEW): integer bps storage, per-exchange stamp duty, FTT no-carry flag, UK ISIN-based stamp duty
- `currency.rs` (NEW): IDEALPRO routing enforced, 6 currencies, stale-rate detection
- `exchange_profile.rs` (NEW): 15 European exchange profiles + XETRA randomized auction window
- `sub_universe_allocator.rs` (NEW): VPIN NaN guard

**Acceptance Tests (AT-134 to AT-153):** same as v21

**Gate**: 25 tests pass; UK ISIN stamp duty on MTF routing verified; Polygon retry verified; 5 paper trading days

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21h | **Depends on**: Phase 18
*(unchanged from v21)*

**v21 Amendments (retained):**
- JPY decimal precision (0 places, f64)
- IBKR reconnect 15s initial delay post-disconnect

**Deliverables:**
- `asian_exchange.rs` (NEW): 6 exchange profiles + ASX DST dynamic + KRX VI confirmation + JPY 0-decimal precision
- `clock.rs` EXTENDED: 04:45 UTC reconnect handler with 15s initial delay

**Acceptance Tests (AT-158 to AT-173):** same as v21

**Gate**: 20 tests pass; JPY decimal precision verified; ASX DST dynamic verified; reconnect 15s delay verified

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 24h | **Depends on**: Phase 19
*(+1h vs v21 for UnauthorizedPnLStream Telegram alert + WAL event)*

**v22 Amendment:**

- **UnauthorizedPnLStream Telegram alert (v22-FIX-8):** `CarryMonitor` maintains `authorized_carry_conids: HashSet<ConId>` (v21-FIX-10). Any `account_pnl_update` with conid NOT in authorized set: (a) **First occurrence per conid:** send Telegram `UnauthorizedPnLStream` alert — `"Unknown conid {conid} received PnL update — verify routing"`. Write `WalPayload::UnauthorizedPnLStream { conid, first_seen_ts }` to WAL. (b) **Subsequent occurrences of same conid:** silently discard, increment `discarded_pnl_updates_count`. Report total count in daily Telegram report. This distinguishes genuine routing bugs (first-occurrence alert) from expected manual portfolio pollution (silent after first).

**v21 Amendments (retained):**
- CarryMonitor HashSet whitelist (v21-FIX-10)
- reqPnL 3-min update interval staleness detection

**Deliverables:**

- `overnight_carry.rs` (NEW): full state machine
  - **CarryMonitor HashSet whitelist (v21-FIX-10)**
  - **UnauthorizedPnLStream: first-occurrence Telegram alert + WAL event (v22-FIX-8)**
  - **reqPnL staleness detection (v21)**
  - Account-level reqPnL + FTT no-carry enforcement

**Acceptance Tests (AT-179 to AT-198):**
- AT-179 through AT-197: same as v21
- **AT-198: UnauthorizedPnLStream first occurrence: inject unknown conid → Telegram alert sent (mock Telegram); WAL event WalPayload::UnauthorizedPnLStream written; second injection of same conid → NO Telegram alert; discarded_count incremented**

**Gate**: 25 tests pass; HashSet whitelist verified; first-occurrence Telegram alert verified (single alert per conid); PnL staleness detection verified

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13h | **Depends on**: Phase 20
*(unchanged from v21)*

**Deliverables**: Same as v21 Phase 21 (DCC-GARCH weights, artifact validation, ES futures tick, active_state.wal hook for step 10).

**Acceptance Tests (AT-204 to AT-215):** same as v21.

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 33h | **Depends on**: Phase 21
*(+3h vs v21 for active_state.wal atomic write, CRC32 load verification, S3 backup cron, additional tests)*

**v22 Amendments:**

- **active_state.wal atomic write (v22-FIX-4):** Ouroboros step 10 writes `calibration/active_state.wal` via atomic pattern: (1) Write full JSON to `calibration/active_state.wal.tmp`. (2) Compute CRC32 of full file content. Append `{"_crc32": <hex>}` as last line. (3) `os::rename("active_state.wal.tmp", "active_state.wal")` — atomic on POSIX. (4) Old .wal is only deleted when rename succeeds. Engine startup: (1) Load `active_state.wal`. (2) Strip last line. (3) Recompute CRC32 of remaining content. (4) Compare vs stored CRC32. If mismatch → log `ActiveStateCorrupt` → fall back to historical WAL replay.
- **V2 calibration/ S3 backup (v22-IN7):** Add to Supercronic: daily at 04:00 UTC: backup `calibration/` directory + `active_state.wal` to S3 bucket (same bucket as V1 backup). Script: `aws s3 sync /app/calibration/ s3://nzt48-backups/aegis-v2/calibration/`. Prevents total state loss on EBS failure.
- **ArcSwap exchange-hours safety (v21, retained):** SIGHUP config reload validates open positions against new config.
- **PDF report cleanup cron (v21, retained):** Daily at 03:00 UTC.

**Deliverables:**

- **active_state.wal atomic write pattern (v22-FIX-4):** tmp + CRC32 + os::rename on write; CRC32 verify on load; `ActiveStateCorrupt` WAL event on mismatch
- **V2 calibration S3 backup cron** via Supercronic **(v22-IN7)**
- **ArcSwap exchange-hours validation** on SIGHUP **(v21)**
- **PDF cleanup cron** **(v21)**
- All v20/v21 deliverables retained (SIGTERM drill, WAL compaction, NTP check, chaos suite, Prometheus, rate limiter audit)

**Acceptance Tests (AT-216 to AT-232):**
- AT-216 through AT-226: same as v21
- **AT-227: active_state.wal fast-path: engine restart with valid active_state.wal < 25h old and correct CRC32 → positions loaded in <100ms; no historical WAL parse**
- **AT-227b: active_state.wal CRC32 mismatch: write partial content to active_state.wal (simulate crash mid-write); engine detects CRC mismatch → logs ActiveStateCorrupt → falls back to historical WAL replay**
- **AT-228: active_state.wal stale: restart with active_state.wal > 25h old → falls back to historical WAL replay; logs ActiveStateStale**
- **AT-229: SIGHUP config reload: new config closes exchange with open position → reload rejected, ConfigReloadRejected logged; old config retained**
- **AT-230: PDF cleanup cron: after 35 days, PDFs older than 30 days removed; newest 30 PDFs retained**
- **AT-231: S3 backup cron: dry-run `aws s3 sync` at 04:00 UTC; verify calibration/ artifacts present in mock S3 path**

**Gate**: 23 tests pass; 48h continuous paper run without HALT; active_state.wal fast-path startup verified (<100ms); CRC32 mismatch recovery verified; S3 backup cron verified; ArcSwap exchange-hours safety verified; PDF cleanup verified

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(Romano-Wolf single-hypothesis correction retained from v20)*

> **The Engineering-vs-Alpha Boundary (Gemini Institutional Syndicate, confirmed):**
> Phases 8-22 guarantee the chassis. They eliminate every known infrastructure failure mode: corrupted execution timing, broken data feeds, WAL corruption on restart, IBKR API misuse, clock bugs, OOM crashes. The 0% win rate on 52 prior paper trades is attributable to these infrastructure failures — not to signal quality. Phase 23 is where that hypothesis is tested.
>
> **If WR ≥ 40% and Sharpe > 0:** The underlying S15 signal math has genuine edge. Live capital is granted.
> **If WR < 40% or Sharpe ≤ 0:** The infrastructure was not the only problem. The signal generation logic must be rewritten before live capital is deployed. The Crucible will reject it before a single real pound is lost.

**Deliverables (7 test suites):**

1. **Suite 1 — Trade Gate**
   - WR ≥ 40% on last 100 paper trades
   - Single-hypothesis t-test (N=1): `t-stat = mean_pnl / (std_pnl / sqrt(100))` ≥ 2.0
   - Bootstrap resampling: 1,000 iterations, 95th percentile CI on WR and Sharpe
   - Sharpe (cost-adjusted) > 0; Max drawdown < 8%; Zero HALT from system errors

2. **Suite 2 — SIGTERM Flatten Drill**
   - Kill container mid-position (3 open positions); flat on restart, WAL consistent, no orphans; 5 repetitions

3. **Suite 3 — 48h Paper Shadow Run**
   - Shadow book vs broker: max divergence < £50; all mode transitions logged with latency < 50ms

4. **Suite 4 — Chaos Engineering**
   - Python bridge crash, IBKR kill, Redis kill — all recovered in sequence

5. **Suite 5 — ISA Compliance Audit**
   - 200 synthetic order intents; 0 short orders, 0 Taiwan/China/India; 0 exceeding £20k
   - WAL `CorporateActionVeto` fires for synthetic blocklist ticker; `isa_compliance_audit.json` generated

6. **Suite 6 — Line Budget Stress Test**
   - proptest 1,000 sequences; `active_line_count <= 100` NEVER violated (AtomicUsize verified)
   - Scanner Conservation Rule holds; HotScanner/RotationScanner → 0 underlying lines

7. **Suite 7 — Full Mode Cycle**
   - 24h paper run: ModeA → DARK → ModeB → ModeB+ → ModeC → DARK
   - DST boundary handled (chrono-tz verified); NZX pre-subscribed at 22:55 UTC
   - Ouroboros completes all 10 steps within DARK; intraday_spread_cache.json updated

**Gate**: All 7 suites pass with written sign-off. 100 validated paper trades. No P0 bugs open. **APPROVED FOR LIVE CAPITAL** stamp.

> **Signal Rewrite Protocol (if Crucible fails):** If WR < 40% after clean 100-trade run with zero infrastructure HALT events, signal generation (HotScanner, RotationScanner, CUSUM thresholds, QuoteImbalance decay constants) is rewritten and Crucible rerun. Infrastructure (Phases 8-22) is NOT rebuilt. Only signal math is under revision.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ (all passing) |
| **8** | Pre-Conditions + P0 (SC-01→SC-17 + v22 amendments) | **48h** | **NEXT** | Unit tests per SC item |
| **11** | 5-Mode Clock + SubscriptionManager (AtomicUsize, no reqOpenOrders) | **22h** | NOT STARTED | AT-01→19 |
| **12** | Smart Router + Intraday cache + Zero-spread guard + XETRA | **21h** | NOT STARTED | AT-19→41 |
| **13** | HotScanner + RotationScanner (ATR σ_noise + neutral resume + OFI aggregator) | **25h** | NOT STARTED | AT-41→60 |
| **14** | Infinite Chandelier (TWAP cancel on exit) + Executioner V2 | **22h** | NOT STARTED | AT-61→75 |
| **15** | RiskGate 31 Vetoes + CVaR (Maillard + EVT POT GPD) | **20h** | NOT STARTED | AT-76→95 |
| **16** | Ouroboros (Intraday spread, EXCHANGE_TIMEZONE_MAP, atr_14_pct, asyncio fix) | **26h** | NOT STARTED | AT-98→120 |
| **17** | Telemetry (async Redis, HALT auth, asyncio fix, data_fetch.py fix) | **15h** | NOT STARTED | AT-119→130 |
| **18** | European Equities + UK ISIN stamp duty + Polygon retry | **21h** | NOT STARTED | AT-134→153 (+5 paper days) |
| **19** | Asia-Pac MODE A + JPY precision + reconnect delay | **21h** | NOT STARTED | AT-158→173 |
| **20** | Carry State Machine (HashSet + PnL staleness + UnauthorizedPnLStream alert) | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence | **13h** | NOT STARTED | AT-204→215 (+5 paper days) |
| **22** | Institutional Hardening (active_state.wal atomic + S3 backup) | **33h** | NOT STARTED | AT-216→232 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~345h** | | **~235 acceptance tests** |

*(+8h vs v21: v22-FIX-1 AtomicUsize+SemaphorePermitGuard +1.5h, v22-FIX-3 OFI aggregator +1.5h, v22-FIX-9 EVT POT GPD +2h, v22-FIX-4 CRC32 atomic WAL +1h, supporting changes +2h)*

**At 20h/week**: ~17.3 weeks to live capital
**At 40h/week**: ~8.6 weeks to live capital

---

### Drawdown Tier Reference

| Tier | Kelly Sizing | New Entries | Existing Positions | Trigger |
|------|-------------|-------------|-------------------|---------|
| NORMAL | `kelly_scale × 100%` | Allowed | Managed normally | Default |
| **YELLOW** | `kelly_scale × 50%` | Blocked | Managed normally (exits still fire) | Ouroboros failure; drawdown −3% |
| **ORANGE** | 0% | Blocked | Close all positions at market | Drawdown −5% |
| **RED** | 0% | Blocked | Full halt (no exits, no orders) | Drawdown −8%; manual RESUME only |

*`kelly_scale = max(0.1, min(1.0, validated_trades / 250))` — ramps from 0.1× at 0 trades to 1.0× at 250 trades. CVaR limit also scales with kelly_scale. Yellow halves whatever the ramp currently produces.*

---

### New Files Created in Phases 8-23
*(v21 list with v22 amendments)*

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — AtomicUsize(SeqCst) + Semaphore + SemaphorePermitGuard
├── mode_controller.rs         (Phase 11) — chrono-tz, no reqOpenOrders
├── smart_router.rs            (Phase 12) — intraday_spread_cache, zero-spread guard, 800ms timeout
├── isa_gate.rs                (Phase 12) — April 6 boundary
├── hot_scanner.rs             (Phase 13) — Volume-weighted OFI during overflow, neutral QI resume
├── rotation_scanner.rs        (Phase 13) — Gaussian-Gaussian TS, ATR-percentile σ_noise
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — TWAP cancel on Chandelier hit
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — Maillard K>S²-1, EVT POT GPD fallback, CVaR-Kelly scaling
├── overnight_carry.rs         (Phase 20) — HashSet<conid> whitelist, UnauthorizedPnLStream alert
├── currency.rs                (Phase 18) — IDEALPRO routing
├── exchange_profile.rs        (Phase 18) — XETRA random auction window
├── transaction_tax.rs         (Phase 18) — UK ISIN stamp duty
├── sub_universe_allocator.rs  (Phase 18) — VPIN NaN guard
└── asian_exchange.rs          (Phase 19) — JPY 0-decimal precision

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — sequential steps, EXCHANGE_TIMEZONE_MAP, asyncio safe restart
├── ouroboros/symbology_mapper.py  (Phase 8) — reverse split handling
├── telegram_reporter.py       (Phase 17) — asyncio safe restart, HALT auth
├── pdf_generator.py           (Phase 17)
├── shadow_book.py             (Phase 17) — £50 threshold
├── cross_timezone.py          (Phase 21) — artifact validation
└── asia_universe.py           (Phase 21)

config/
├── european_exchange_profiles.toml  (Phase 18)
├── european_routing_table.toml      (Phase 18)
├── transaction_tax.toml             (Phase 18) — includes UK ISIN stamp duty
├── asian_exchange_profiles.toml     (Phase 19)
└── asian_routing_table.toml         (Phase 19)

calibration/
├── weights.json               (Ouroboros step 10)
├── asia_cross_tz.json         (Ouroboros step 9)
├── corp_action_blocklist.json (Ouroboros step 2 — dates in exchange-local timezone per EXCHANGE_TIMEZONE_MAP)
├── intraday_spread_cache.json (Ouroboros step 3 — 5-day median intraday spread; renamed from eod_spread_cache.json)
├── asset_volatility.json      (Ouroboros step 8 — atr_14_pct + atr_14_abs per asset)
├── exchange_times.json        (Ouroboros step 1 — dynamic DST)
├── active_state.wal           (Ouroboros step 10 — atomic write: tmp+CRC32+rename)
└── compaction_manifest.json   (Phase 22)
```

---

## TDD MANDATE (NON-NEGOTIABLE)

> Confirmed by Gemini Institutional Syndicate: *"Hold the agent strictly to Test-Driven Development (TDD) rules. Do not let it advance to Phase 11 until all structural foundation items in Phase 8 are coded, tested, and verified."*

**Rule**: For every SC item, the sequence is:
1. Write the test first (failing)
2. Write the implementation
3. Run `cargo test` — must pass before touching the next SC item
4. No skipping, no batching, no "I'll test it all at the end"

**Gate enforcement**: The Phase 8 gate document MUST contain actual `cargo test` output showing each SC item's tests passing. Fabricated output = automatic rejection. The agent may not claim a gate is passed without pasting the literal terminal output.

This applies to every phase gate: 8, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23.

---

## TERMINAL KICKOFF PROMPT (Phase 8)

Paste this into a new Claude Code terminal session to begin Phase 8 implementation:

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v22.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v22.md

IMPLEMENTATION TOOLING MANDATE: Use accept-edits mode ONLY. Do NOT use bypass-permissions.
The Ralph Wiggum stop hook is retained for automated retry. All bash commands require
manual approval. This is non-negotiable per v22-FIX-6.

TDD MANDATE: For each SC item — write the test first (failing), implement, run cargo test
(passing), THEN move to the next. Never batch tests. Never advance without a green test.
This is non-negotiable.

Implement all 17 SC items in order. Write unit tests for each. Run cargo test after each
SC item before proceeding to the next.

SC-01: SIGTERM handler in main.rs — ctrlc crate, flatten positions → wait 30s for fills →
write SystemShutdown WAL event → exit

SC-01a: docker-compose.yml — add `stop_grace_period: 60s` to the aegis-v2 service definition

SC-02: SubscriptionManager skeleton in subscription_manager.rs — use
AtomicUsize(Ordering::SeqCst) for active_line_count (NOT RwLock, NOT Mutex).
Use tokio::sync::Semaphore(100) for the ≤100 budget constraint.
Implement SemaphorePermitGuard(Arc<Semaphore>) with Drop::drop() → add_permits(1).
All permit acquisitions MUST go through SemaphorePermitGuard — never acquire raw permit.
Unit test 1: 1000 concurrent subscribe/cancel sequences → active_line_count never exceeds 100.
Unit test 2: acquire SemaphorePermitGuard → simulate panic via catch_unwind →
available_permits() == 100 after unwind. (v22-FIX-1 + v22-FIX-5)

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
DUAL PATH — v22 AMENDMENT:
  (a) OFI path: VOLUME-WEIGHTED AGGREGATOR (NOT suspension). Accumulate bid_vol_sum +=
  tick.bid_vol and ask_vol_sum += tick.ask_vol for each dropped tick. After buffer drains,
  emit synthetic OFI ratio = (bid_vol_sum - ask_vol_sum) / (bid_vol_sum + ask_vol_sum + 1e-9).
  Feed this ratio into QI EWMA. OFI calculation NEVER paused. Emit
  WalPayload::QuoteImbalanceCompressed { ticker_id, bid_vol_sum, ask_vol_sum, dropped_count }.
  (v22-FIX-3 — supersedes v21-FIX-6 QI suspension)
  (b) Chandelier path: aggregate H/L/V into current OHLCV bar: bar.high = max(bar.high,
  tick.last); bar.low = min(bar.low, tick.last); bar.volume += tick.volume. (v20-FIX-13 retained)
Unit test: inject 200 overflow ticks with known bid_vol/ask_vol → verify emitted OFI ratio
matches manual (Σbid_vol - Σask_vol)/(Σbid_vol + Σask_vol) ± 0.001

SC-10: Internal cost-basis tracker: CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }.
avg_cost = total_cost / total_shares. Nightly clear at Ouroboros step 1 + IBKR reqPositions
resync.

SC-11: SubscriptionManager active_line_count: AtomicUsize. Increment on reqMktData ACK
(fetch_add(1, Ordering::SeqCst)), decrement on cancelMktData ACK
(fetch_sub(1, Ordering::SeqCst)). assert!(count <= 100) before every new subscription.
DO NOT call reqOpenOrders — this is the wrong API and causes Error 3200 ban.
AtomicUsize is sole truth. (v21-FIX-2)

SC-12: symbology_mapper.py — rules (a) space→dot, (b) LSE suffix→prefix, (c) exchange
pass-through, (d) preferred shares BAC PR D → BAC/PD, (e) reverse mapping
to_ibkr(polygon_symbol), (f) reverse split: adjust total_shares /= split_factor,
price_basis *= split_factor

SC-13: (a) dynamic Kelly ramp: kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))
in risk_arbiter.rs; (b) POLARS_MAX_THREADS=2 environment variable in docker-compose.yml
under aegis-v2 service; (c) SplitAdjustment WalPayload variant

SC-14: reqMarketDataType(3) — add client.req_market_data_type(3) as THE FIRST CALL in
ibkr_broker.rs::connect() before any subscribe_bars() or reqMktData calls

SC-15: StrategyId enum extension — add StrategyId::HotScanner and StrategyId::RotationScanner
to types/enums.rs; verify WalPayload::PositionOpened and PositionClosed include strategy_id

SC-16: shm_size: '2gb' — add to aegis-v2 service in docker-compose.yml. Verify inside
container: `df -h /dev/shm` shows ≥2GB. (v21-FIX-5)

SC-17: WalPayload::QuoteImbalanceCompressed variant — add to types/wal.rs with fields:
ticker_id: TickerId, bid_vol_sum: f64, ask_vol_sum: f64, dropped_count: u32.
Wire into channel.rs overflow path (SC-09a). Note: this replaces QuoteImbalanceInvalidated
from v21 — OFI is compressed, not suspended. (v22-FIX-3)

After all 17 items have passing tests:
- Run cargo test (all tests must pass — paste literal output)
- Run docker build (must succeed)
- Verify docker-compose.yml has ALL THREE: stop_grace_period: 60s, POLARS_MAX_THREADS=2,
  shm_size: '2gb'
- Run `docker exec aegis-v2 df -h /dev/shm` → shows ≥2GB
- Run a 30-minute paper session to verify SC-01 SIGTERM drill end-to-end
- Verify reqMarketDataType(3) appears as FIRST IBKR call in paper session logs
- Verify grep on subscription_manager.rs: NO RwLock, NO reqOpenOrders
- Verify SemaphorePermitGuard panic test passes (available_permits == 100 after catch_unwind)
- Verify OFI volume-weighted aggregator test passes (ratio matches manual calculation ±0.001)

Do NOT start Phase 11 until Phase 8 gate is fully signed off with pasted cargo test output.
```

---

*AEGIS_MASTER_PLAN_v22.md — Generated 2026-03-09*
*Supersedes: AEGIS_MASTER_PLAN_v21.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v21.md (Gemini G3 "Institutional Syndicate" 200-bullet adversarial audit)*
*10 v22 priority fixes: G3-P1 through G3-P10 from Gemini G3 priority matrix*
*1 critical safety fix: G3-CRITICAL-SAFETY (bypass-permissions → accept-edits)*
*Total acceptance tests: ~235 (vs 230 in v21)*
*Total remaining hours: ~345h (vs 337h in v21, +8h for v22 additions)*
