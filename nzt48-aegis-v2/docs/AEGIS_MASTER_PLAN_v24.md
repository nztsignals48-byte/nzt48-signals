# AEGIS V2 — MASTER PLAN v24
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 24.0 | **Date**: 2026-03-09 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v23. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v23.md — the Gemini G5 "Institutional Syndicate" 200-bullet adversarial audit of v23. New fixes are marked **[v24-FIX-N]** for traceability. The G5 audit found 10 genuine priority fixes (G5-P1 through G5-P10) plus 3 improvement items and ~14 minor operational fixes. The remaining 173 bullets were duplicates of prior fixes, academic deferrals, or FUD.

---

## v24 DELTA — G5 PRIORITY FIXES

| Fix | G5 ID | Trap | What was wrong in v23 | What v24 does |
|-----|-------|------|-----------------------|---------------|
| **v24-FIX-1** | G5-P1 | Watchdog exit(1) destroys WAL | SC-18-W called `std::process::exit(1)` — bypasses all Drop traits, skips SC-01 SIGTERM graceful shutdown, corrupts WAL buffers, orphans positions on IBKR. | Replace `exit(1)` with `unsafe { libc::kill(libc::getpid(), libc::SIGTERM) }` — sends SIGTERM to self, triggers SC-01 graceful shutdown sequence. Watchdog inner loop wrapped in `std::panic::catch_unwind` to survive inner panics. |
| **v24-FIX-2** | G5-P2 | T+2 hardcoded: US SEC T+1 since May 2024 | `settlement_lag_days=2` applied to ALL exchanges including NYSE/NASDAQ. US equities have been T+1 since May 28, 2024 (SEC Rule 15c6-1). Corp action veto fires 1 day too early for US assets → ISA risk. | Per-exchange settlement lag in EXCHANGE_TIMEZONE_MAP: NYSE=1, NASDAQ=1. All European/Asian exchanges remain T+2. Add AT-111d (NYSE T+1 test). |
| **v24-FIX-3** | G5-P3 | 48h staleness guard fires every Monday | Weekend = 59h wall-clock. Every Monday morning: cache flagged stale → full ETP fallback → no direct equity alpha. Fires on every bank holiday too. | Staleness guard is **market-open-hours-aware**: if exchange was closed since `generated_at`, cache is valid regardless of wall-clock age. Specifically: `stale = (now - generated_at > 72h) AND exchange_open_since_generated_at`. |
| **v24-FIX-4** | G5-P4 | WAL replay timeout → ORANGE forces liquidation | 30s WAL replay timeout triggered `DrawdownTier::Orange` (close all positions at market). Slow EBS boot → 31s replay → full portfolio liquidated at market open. | Timeout → `DrawdownTier::Yellow` (no new entries; existing positions managed by existing stops). Alert: "WAL replay timeout. Yellow mode. Manual RESUME required." Orange reserved for drawdown events only. 7-day stale WAL + fast-path fail → also Yellow. |
| **v24-FIX-5** | G5-P5 | EVT ξ cap at 0.5 blinds RiskGate to flash crash | Clamping ξ ≤ 0.5 artificially imposes finite variance on infinite-variance distributions. Flash crashes in 3x ETPs show ξ = 0.6-0.9. RiskGate approves full sizing into structural liquidity vacuum. | Remove 0.5 cap. Let ξ float from MLE. If `xi >= 1.0`: log `GpdInfiniteVariance { xi_mle }` → return `CVaRExceeded` immediately (no sizing). Lower bound: `xi.max(-0.5)` only (numerical stability). |
| **v24-FIX-6** | G5-P6 | SemaphorePermitGuard: mem::forget + add_permits double-return | `mem::forget` inside `Drop` is redundant and dangerous. The pattern risks double-return (permit inflation above 100) or permanent leak (permit lost if panic before forget). | Simplified RAII: `SemaphorePermitGuard { _permit: OwnedSemaphorePermit }`. Natural `Drop` returns 1 permit to Semaphore automatically via tokio's RAII. Zero `mem::forget`, zero `add_permits`, zero risk. |
| **v24-FIX-7** | G5-P7 | OFI uses trade volume, not quote size changes | SC-09 accumulated `bid_vol_sum`/`ask_vol_sum` from Last/LastSize trade ticks. Cont et al. (2014) define OFI strictly from ΔBidSize/ΔAskSize at BBO — decoupled from trades entirely. | Accumulate `bid_size_delta` and `ask_size_delta` from IBKR BidSize/AskSize quote tick types. Rename WAL fields. Label as COF (Compressed Order Flow) in comments — honest about the approximation during overflow. |
| **v24-FIX-8** | G5-P8 | 14-day ATR includes overnight gap: wrong for intraday TS | `ts_prior_sigma_0 = max(0.05, atr_14_pct × 3.0)` uses 14-period daily ATR which includes overnight gaps. Overnight variance inflates the prior, over-penalizing high-edge volatile ETPs and starving them of scanner lines. | Compute `intraday_atr_14_pct = mean(bar.high - bar.low, last 14 sessions) / mid_price` — no gap variance. Write to asset_volatility.json. `ts_prior_sigma_0 = max(0.05, intraday_atr_14_pct × 3.0)`. `σ_noise = max(0.02, intraday_atr_14_pct × 1.5)`. |
| **v24-FIX-9** | G5-P9 | CRC32 sentinel at end of JSON: serde_json panics on torn write | `active_state.wal` ended with `{"__aegis_crc32__": "hex"}`. Torn write (crash before sentinel) → serde_json parse panic on truncated JSON. CRC32 unreachable. | **Prefix-header format**: line 1 = CRC32 hex string, line 2+ = JSON payload. CRC32 validated BEFORE serde_json parse. Torn write → valid CRC32 header + invalid JSON → caught cleanly → WAL replay. |
| **v24-FIX-10** | G5-P10 | asyncio thread restart: module-level singletons attached to dead loop | `threading.Thread` restart creates fresh event loop in new thread, but module-level `aiohttp.ClientSession`, `redis.asyncio` connections created in old loop raise `RuntimeError: Task attached to a different event loop`. | Move ALL session/client creation INSIDE `async def fetch_all_tickers()`. No module-level asyncio singletons. Each thread restart creates fresh connections. Context managers ensure cleanup on exit. |

**v24-MINOR-FIXES** (operational):
- **Ordering::Relaxed** for AtomicUsize telemetry (G5-I1 — since Semaphore enforces budget)
- **Telegram keep-alive 30s ping** + 429 backoff + C-binding exception catch (Phase 17)
- **Nordic lit venue routing** in exchange_profile.rs (OMX Stockholm → no dark MTFs)
- **TOML u32 serde** explicit deserializer for transaction_tax.toml (prevents i64 panic)
- **Artifact freshness**: 26h → 96h (covers weekends) in Phase 21
- **DCC-GARCH RwLock**: timeout + re-initialize on poison (Phase 15)
- **Polars .optimize()** before .collect() in Ouroboros pipeline (Phase 16)
- **Polygon /dividends timeout**: retain previous blocklist (Phase 16)
- **shutil.move cross-device fallback** for blocklist atomic write (Phase 16)
- **Yellow tier alert throttle**: max 1 alert per 4h (Phase 16)
- **TWAP min slice floor**: max(alpha_halflife_ms, 100ms) (Phase 14)
- **contractDetailsEnd handler** in subscription_manager.rs (Phase 11)
- **mode_controller channel** capacity: 16 → 64 (Phase 11)
- **ISA hard-block**: const HashSet (not heap String) (Phase 12)
- **WAL replayer**: skip corrupted events with WalEventCorrupt log (Phase 22)
- **Prometheus**: explicit Gauge/Counter metric type labels (Phase 22)
- **TWAP early-close**: 100ms minimum slice floor (Phase 14)

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

*(unchanged from v23)*

| Component | Status | Critical Issues |
|-----------|--------|----------------|
| **S15 daily_target.py** | ACTIVE | 0% win rate on 52 paper trades — execution timing root cause |
| **S3 mean_reversion.py** | DORMANT | Hard ETP veto correct; V2.1 comment removed (SC-07) |
| **chandelier_exit.py** | ACTIVE | Le Beau 5-rung; Redis-persisted (7-day TTL) |
| **cross_asset_macro.py** | ACTIVE | C-06 fixed; VIX 5-min cache; weekly HMM refit |
| **ml_meta_model.py** | DISABLED | AEGIS 0-05: circular feedback; fabricated data |
| **uk_isa/ (15 files)** | ACTIVE | 12 leveraged ETPs; lse_registry; correlation_engine |
| **sprint6_live_gate.py** | NOT MET | 0% WR; Romano-Wolf criteria fail; need 63+ MTRL days |
| **state_manager.py** | ACTIVE | Redis SSOT V8.0 with Lua atomicity |

**V1 Critical Bugs** (unchanged from v23)

---

### 1.2 V2 Rust Engine — Complete Module Inventory

**Status: Phases 1-7 COMPLETE. ~9,000 LOC. 147+ tests.**

*(unchanged from v23 — see v23 for full table)*

---

## PART 2 — COMBINED ADVERSARIAL AUDIT TRIAGE SUMMARY

### 2.1 Combined P0 + P1 Matrix (all versions)

**P0 — Fatal:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-1 | Docker SIGKILL at 10s | `stop_grace_period: 60s` | **v20-FIX-1, Phase 8** |
| P0-2 | Polars vCPU starvation | `POLARS_MAX_THREADS=2` | **v20-FIX-2, Phase 8** |
| P0-3 | Half-Kelly + Min Entry = 0 trades | Dynamic Kelly ramp floor 0.1× | **v20-FIX-3, Phase 8/15** |
| P0-4 | WAL compaction severs open positions | active_state.wal nightly rewrite | **v20-FIX-4 + v21-FIX-9, Phase 22** |
| P0-5 | reqPnL 1-per-connection limit | Account-level reqPnL | **v20-FIX-5, Phase 20** |
| P0-6 | clock.rs BST missing % 86400 | chrono-tz Europe::London | **v20-FIX-6, Phase 11** |
| P0-7 | RwLock writer starvation | AtomicUsize(Relaxed) + Semaphore(100) | **v24-FIX-6 refines v23-FIX-1, Phase 8** |
| P0-8 | No reqMarketDataType(3) | First call in ibkr_broker.rs::connect() | **v20-FIX-8, Phase 8** |
| P0-9 | Heartbeat only in DARK | Engine-side 30-min Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent | HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |
| P0-11 | reqOpenOrders Error 3200 ban | Internal AtomicUsize only | **v21-FIX-2, Phase 11** |
| P0-12 | Docker /dev/shm 64MB → Bus error | shm_size: '2gb' | **v21-FIX-5, Phase 8** |
| P0-13 | bypass-permissions LLM root access | accept-edits ONLY | **v22-FIX-6, Process** |
| P0-14 | Engine deadlock: no watchdog | std::thread watchdog → self-SIGTERM | **v24-FIX-1 refines v23-FIX-11, Phase 8** |
| P0-15 | Watchdog exit(1) corrupts WAL | libc::kill(SIGTERM) instead of exit(1) | **v24-FIX-1, Phase 8** |

**P1 — High:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | EOD spread cache + weekend stale | Intraday cache + market-hours staleness guard | **v22-FIX-2 + v24-FIX-3, Phase 12** |
| P1-2 | Telegram polling dies silently | Infinite retry + keep-alive + C-binding catch | **Phase 17** |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | QI suspension at peak alpha | COF aggregator (BidSize/AskSize delta) | **v22-FIX-3 + v24-FIX-7, Phase 8** |
| P1-6 | σ_noise 30-day lag + overnight gap | intraday_atr_14_pct (High-Low only) | **v22-FIX-10 + v24-FIX-8, Phase 13** |
| P1-7 | Corp action timezone + settlement lag | EXCHANGE_TIMEZONE_MAP + per-exchange T+1/T+2 | **v22-FIX-7 + v24-FIX-2, Phase 16** |
| P1-8 | WAL compaction unbounded | Nightly atomic rewrite | **v21-FIX-9 + v22-FIX-4, Phase 22** |
| P1-9 | reqPnL manual holdings crash | HashSet<conid> whitelist | **v21-FIX-10, Phase 20** |
| P1-10 | CF domain violation + EVT ξ uncapped | Maillard gate + GPD ξ-free (ξ≥1 → CVaRExceeded) | **v21-FIX-3 + v22-FIX-9 + v24-FIX-5, Phase 15** |
| P1-11 | Cost basis wrong after split | Nightly clear + reqPositions resync | Phase 8 |
| P1-12 | Dust slippage on illiquid | Peg-to-Mid TIF=3min | Phase 8 |
| P1-13 | AtomicUsize leaks on dropped ACK | Internal tracking only | **v21-FIX-2, Phase 11** |
| P1-14 | FTT intraday exemption | Flag FTT entries as no-carry | Phase 18/20 |
| P1-15 | NZX misses opening auction | Pre-subscribe at 22:55 UTC | Phase 19 |
| P1-16 | ISA tax year Jan 1 not April 6 | April 6 boundary in isa_gate.rs | Phase 12 |
| P1-17 | HKEX board lot → 0-share | ETP fallback when lot×price > Kelly | Phase 12 |
| P1-18 | Polars OOM parallel steps | Sequential step enforcement | Phase 16 |
| P1-19 | Carry allocator assumes 3 not 6 | Dynamic: 100 − (carry_count × 2) | **v20-FIX-14, Phase 20** |
| P1-20 | Semaphore permit leak + mem::forget risk | SemaphorePermitGuard natural RAII Drop | **v22-FIX-5 + v24-FIX-6, Phase 8** |
| P1-21 | active_state.wal non-atomic write | Prefix-header CRC32 + tmp + rename | **v22-FIX-4 + v24-FIX-9, Phase 22** |
| P1-22 | WAL replay timeout → Orange liquidation | Timeout → Yellow; 7-day stale → Yellow | **v24-FIX-4 refines v23-FIX-13, Phase 22** |
| P1-23 | Thompson Sampler σ_0 overnight gap bias | intraday_atr_14_pct × 3.0 for σ_0 | **v24-FIX-8 refines v23-FIX-9, Phase 13** |
| P1-24 | OFI calculated from trade volume not quotes | BidSize/AskSize delta (COF) | **v24-FIX-7 refines v23-FIX-2, Phase 8** |
| P1-25 | T+2 hardcoded: US is T+1 since May 2024 | Per-exchange settlement_lag (NYSE/NASDAQ=1) | **v24-FIX-2, Phase 16** |

---

### 2.2 Binding Architectural Mandates (all versions + v24)

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Polars LazyFrame + Arrow zero-copy. .optimize() before .collect(). | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** crossbeam-channel (cap=50,000). Overflow → COF aggregator (BidSize/AskSize delta; zero-size → 0.5 neutral). **(v24-FIX-7)** | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox.** Token bucket for active ~100 tickers; Polygon for nightly universe. | Phase 8+16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed only when live position exists. | Phase 11 |
| **GEM-A5** | **Drawdown tiers.** Yellow (new entries blocked) / Orange (close all) / Red (full halt). WAL replay timeout → Yellow. | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time via Europe::London. | Phase 11 |
| **v20-A2** | **AtomicUsize(Ordering::Relaxed) for active_line_count telemetry.** Semaphore(100) is the enforcement gate. SemaphorePermitGuard: `{ _permit: OwnedSemaphorePermit }` natural RAII Drop. **(v24-FIX-6 + G5-I1)** | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler.** σ_noise = max(0.02, intraday_atr_14_pct × 1.5). σ_0 = max(0.05, intraday_atr_14_pct × 3.0). **(v24-FIX-8)** | Phase 13 |
| **v20-A4** | **Account-level reqPnL + CarryMonitor whitelist + UnauthorizedPnLStream alert.** | Phase 20 |
| **v21-A1** | **No reqOpenOrders.** Internal AtomicUsize only. | Phase 11 |
| **v21-A2** | **shm_size: '2gb'.** | Phase 8 |
| **v21-A3** | **Maillard CF gate + EVT POT GPD.** ξ uncapped; ξ≥1 → GpdInfiniteVariance → CVaRExceeded. **(v24-FIX-5)** | Phase 15 |
| **v21-A4** | **COF aggregator on overflow.** BidSize/AskSize delta (not trade volume). Zero-size → ratio=0.5. **(v24-FIX-7)** | Phase 8 |
| **v21-A5** | **active_state.wal prefix-header format.** Line 1 = CRC32 hex. Line 2+ = JSON. CRC before parse. **(v24-FIX-9)** | Phase 22 |
| **v22-A1** | **EXCHANGE_TIMEZONE_MAP + per-exchange settlement_lag.** NYSE/NASDAQ=T+1; EU/Asia=T+2. **(v24-FIX-2)** | Phase 16 |
| **v22-A2** | **intraday_spread_cache.json + market-hours staleness guard.** **(v24-FIX-3)** | Phase 12/16 |
| **v22-A3** | **accept-edits ONLY.** No bypass-permissions. | Process |
| **v23-A1** | **std::thread watchdog.** Stale >120s → self-SIGTERM via libc::kill. Watchdog loop wrapped in catch_unwind. **(v24-FIX-1)** | Phase 8 |
| **v23-A2** | **WAL replay timeout 30s → Yellow (not Orange).** 7-day stale + fast-path fail → Yellow. **(v24-FIX-4)** | Phase 22 |
| **v24-A1** | **Intraday ATR (High-Low only).** `intraday_atr_14_pct` excludes overnight gap variance. Used for σ_noise, σ_0, and ts_prior_sigma_0. | Phase 13/16 |
| **v24-A2** | **COF not OFI during overflow.** Honest labeling: Compressed Order Flow accumulates BidSize/AskSize deltas, not trade volume. True OFI requires continuous L1 quote stream without overflow. | Phase 8 |

---

### 2.3 Deferred (Post-Crucible)

*(v23 defer table + v24 additions)*

| Finding | Reason |
|---------|--------|
| All prior deferred items | Unchanged from v23 |
| VIX futures as CBOE feed fallback | Phase Q2+ |
| Synthetic put hedge on carry positions | Phase Q2+ options |
| ELK/Fluentbit structured logging | Phase Q2+ ops tooling |
| SGX SiMS TIF pre-close auction | Phase Q2+ |
| Garman-Klass intraday volatility estimator | Phase Q2+ enhancement |
| Trade-clock EWMA (volume-based decay) | Phase Q2+ signal research |

---

## PART 3 — PHASE PLAN

### Numbering Convention
- **Phases 1-7**: COMPLETE
- **Phase 8**: Next — **19 SC items** (SC-01 through SC-18-W + SC-19-contractDetailsEnd minor)
- **Phases 11-23**: Granular build

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 52h | **Status**: NEXT
*(+2.5h vs v23: v24-FIX-1 watchdog SIGTERM + catch_unwind, v24-FIX-6 guard simplification, v24-FIX-7 COF BidSize/AskSize)*

**Rationale**: All v23 SC items retained. v24 amendments: SC-01 watchdog replaces exit(1) with libc SIGTERM (v24-FIX-1); SC-02 SemaphorePermitGuard simplified to natural RAII + Relaxed ordering (v24-FIX-6, G5-I1); SC-09 COF aggregator uses BidSize/AskSize delta not trade volume (v24-FIX-7).

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: `tokio::signal::ctrl_c()` + `tokio::signal::unix::signal(SignalKind::terminate())`. DO NOT use ctrlc crate (G2-IN12). Flatten → 30s wait → WAL SystemShutdown → exit. | main.rs | v23-FIX-8 |
| **SC-01a** | `stop_grace_period: 60s` in docker-compose.yml | docker-compose.yml | v20-FIX-1 |
| **SC-02** | SubscriptionManager skeleton. `AtomicUsize` for `active_line_count` with **`Ordering::Relaxed`** for ALL operations (fetch_add, fetch_sub, load) — Semaphore(100) is the enforcement gate; AtomicUsize is telemetry. Add comment: `// Semaphore enforces budget. AtomicUsize is telemetry only. Relaxed ordering sufficient.` **`tokio::sync::Semaphore(100)`** for budget. **`SemaphorePermitGuard { _permit: OwnedSemaphorePermit }`** — natural Drop returns permit to Semaphore automatically via tokio RAII. DO NOT use `mem::forget`. DO NOT call `add_permits` manually. The `_permit` field Drop handles everything. **(v24-FIX-6 + G5-I1)**. Unit test AT-18b: 1000 concurrent subscribe/cancel → active_line_count never > 100. Unit test AT-18c (async panic): tokio::spawn 100 tasks each acquiring SemaphorePermitGuard then panic → join all JoinErrors → `semaphore.available_permits() == 100`. | subscription_manager.rs | v24-FIX-6, G5-I1 |
| SC-03 | LineBudget `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket; Polygon for nightly universe | ibkr_broker.rs + data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` — suspended while validated_trades < 250 | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard: filled < £500.0 → Peg-to-Mid TIF=3min → if unfilled → market-sell | exit_engine.rs | v19-FIX-1 |
| SC-07 | Remove V1 S3 reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| **SC-09** | crossbeam-channel (cap=50,000). On TrySendError::Full → DUAL PATH: **(a) COF path (v24-FIX-7):** accumulate `bid_size_delta_sum` from IBKR **BidSize** quote tick type and `ask_size_delta_sum` from **AskSize** quote tick type. These are quote size CHANGES at BBO, NOT Last/LastSize trade ticks. This is the correct academic OFI proxy (Cont et al. 2014). Zero-size guard: `if bid_size_delta_sum == 0.0 && ask_size_delta_sum == 0.0 → emit ratio = 0.5 (neutral)`. Otherwise: `COF = (bid_size_delta_sum − ask_size_delta_sum) / (bid_size_delta_sum + ask_size_delta_sum + 1e-9)`. Emit `WalPayload::QuoteImbalanceCompressed { ticker_id, bid_size_delta_sum, ask_size_delta_sum, dropped_count }`. **(b) Chandelier path:** aggregate H/L/V into current bar. Unit test AT-60: 200 overflow quote-update ticks (BidSize tick type) with known deltas → verify COF ratio matches manual ± 0.001. Unit test AT-60b: 50 overflow ticks with bid_size_delta=0.0, ask_size_delta=0.0 → COF=0.5. | python_bridge.rs + channel.rs + types/wal.rs | v24-FIX-7 |
| SC-10 | CostBasisEntry HashMap; nightly clear + reqPositions resync | portfolio.rs | G-09 |
| SC-11 | AtomicUsize Relaxed tracking; no reqOpenOrders | subscription_manager.rs | v21-FIX-2 |
| SC-12 | symbology_mapper.py — all 6 rules including reverse split | ouroboros/symbology_mapper.py | v19-FIX-2 |
| SC-13 | kelly_scale ramp + POLARS_MAX_THREADS=2 + SplitAdjustment WAL | risk_arbiter.rs + docker-compose.yml | v20-FIX-3 |
| SC-14 | reqMarketDataType(3) as FIRST call in connect() | ibkr_broker.rs | v20-FIX-8 |
| SC-15 | StrategyId::HotScanner + StrategyId::RotationScanner | types/enums.rs | v20-FIX-10 |
| SC-16 | shm_size: '2gb' in docker-compose.yml | docker-compose.yml | v21-FIX-5 |
| SC-17 | WalPayload::QuoteImbalanceCompressed with `bid_size_delta_sum: f64, ask_size_delta_sum: f64, dropped_count: u32` | types/wal.rs | v24-FIX-7 |
| **SC-18-W** | **Watchdog thread (v24-FIX-1):** `std::thread::spawn` (NOT tokio). Track `AtomicU64 LAST_TICK_TS` (updated via `record_tick()` on every market data tick, `Ordering::Relaxed`). Watchdog loop every 60s. **Inner loop body wrapped in `std::panic::catch_unwind` — on inner panic, log to watchdog.log and CONTINUE the outer loop (watchdog thread must not terminate).** If `now() - last_tick_ts > 120s` during market hours → log `"[WATCHDOG] No tick in Xs — DEADLOCK SUSPECTED. Sending SIGTERM."` → write to `/app/logs/watchdog.log` via std::fs → **`unsafe { libc::kill(libc::getpid(), libc::SIGTERM) }`** — this sends SIGTERM to the process, triggering SC-01 graceful shutdown sequence. Add `libc = "0.2"` to Cargo.toml. | watchdog.rs (new) + main.rs + Cargo.toml | v24-FIX-1 |

**Gate**: All 18 SC items + watch coded + tests; `cargo test` passes; grep: no SeqCst/AcqRel (all Relaxed) in subscription_manager.rs; grep: no mem::forget; grep: no add_permits in Drop; grep: no exit(1) in watchdog (only libc::kill); grep: no ctrlc crate; COF uses BidSize/AskSize tick types (not Last/LastSize); AT-60b passes (zero-size → COF=0.5); async panic test passes (available_permits==100 after 100 spawn-panics); watchdog.log created on SIGTERM trigger test

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 22.5h | **Depends on**: Phase 8
*(+0.5h: contractDetailsEnd handler, mode_controller channel 64)*

**v24 Amendments:**
- **contractDetailsEnd handler**: subscription_manager.rs must track `contractDetailsEnd` IBKR callback to know when `reqContractDetails` batching is complete. Without this, the batcher hangs indefinitely on fragmented TCP responses. Handler: when `contractDetailsEnd(reqId)` fires → mark batch complete → release waiting tasks.
- **mode_controller channel**: capacity 16 → 64 to absorb burst transitions without blocking the clock.

**Deliverables:**

- `clock.rs` REWRITTEN — chrono-tz (v20-FIX-6); `now_london()`, TradingMode enum, DST-correct boundaries
- `subscription_manager.rs` (NEW, extends SC-02/SC-03/SC-11 skeleton):
  - **Relaxed** AtomicUsize (v24-FIX-6 + G5-I1) — see SC-02
  - Semaphore(100) + SemaphorePermitGuard natural RAII
  - No reqOpenOrders (v21-FIX-2)
  - **contractDetailsEnd handler** — batch completion signal
  - Proptest: 500 random sequences; Scanner Conservation Rule (GEM-A4)

- `mode_controller.rs` (NEW): capacity=64 channel
- NZX pre-subscribe at 22:55 UTC

**Acceptance Tests (AT-01 to AT-20):**
- AT-01 through AT-18c: same as v23
- **AT-18d: Relaxed ordering: grep subscription_manager.rs for AcqRel → zero matches. All Ordering::Relaxed.**
- **AT-19: contractDetailsEnd: inject 5000-ticker reqContractDetails → batcher waits for contractDetailsEnd signal → completes without hang**
- **AT-20: mode_controller channel: inject 60 rapid mode transitions → no sender block**

**Gate**: 21 tests pass; Relaxed ordering grep confirmed; contractDetailsEnd handler verified; no reqOpenOrders; no mem::forget; natural RAII Drop only

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 22h | **Depends on**: Phase 11
*(+0.5h: market-hours staleness guard, const HashSet for ISA block)*

**v24 Amendments:**

- **Market-hours staleness guard (v24-FIX-3):** SmartRouter loads `intraday_spread_cache.json`. Staleness check: `stale = (now - generated_at > 72h) AND (exchange_was_open_since_generated_at)`. Use `exchange_times.json` (Ouroboros step 1) to determine if LSE was open in the interval. If exchange was closed since `generated_at` (weekend, holiday): cache is valid. If exchange was open but Ouroboros hasn't refreshed in >72h of trading hours: cache is stale → SpreadCacheStale → ETP fallback.
- **ISA hard-block const HashSet**: `static ISA_BLOCKED: phf::Set<&str> = phf_set! { "TWSE", "SSE", "SZSE", "BSE", "NSE" };` — zero heap allocation per check.

**Deliverables:**

- `smart_router.rs` (NEW): all v23 routing logic + market-hours staleness guard + const HashSet
- `isa_gate.rs` (NEW): April 6 boundary; phf const HashSet for hard-blocks

**Acceptance Tests (AT-19 to AT-42):**
- AT-19 through AT-37b: same as v23
- **AT-37c UPDATE: Friday 21:00 UTC cache → Monday 08:00 UTC load: exchange was closed → cache NOT stale → normal routing (not ETP fallback)**
- **AT-37d: Exchange was open for 2 trading days since generated_at → cache IS stale (>72h trading hours) → SpreadCacheStale → ETP fallback**
- **AT-41: ISA hard-block: TWSE ticker → blocked via const phf set; zero heap allocation confirmed via profiler**

**Gate**: 25 tests pass; weekend non-stale verified (AT-37c); trading-hours stale verified (AT-37d); const HashSet verified

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 26h | **Depends on**: Phase 12
*(+0.5h: intraday_atr for σ_noise and σ_0)*

**v24 Amendments:**

- **Intraday ATR for TS noise (v24-FIX-8 + v24-A1):** ALL ATR references in Phase 13 use `intraday_atr_14_pct` (High-Low, no overnight gap) from asset_volatility.json. σ_noise = max(0.02, intraday_atr_14_pct × 1.5). σ_0 = max(0.05, intraday_atr_14_pct × 3.0). This eliminates overnight gap inflation that starved high-edge leveraged ETPs.

- **COF input to HotScanner (v24-FIX-7 + v24-A2):** During overflow, hot_scanner.rs receives COF ratio from QuoteImbalanceCompressed event (bid_size_delta_sum, ask_size_delta_sum). Zero-size → COF=0.5 (neutral, EWMA unchanged). Note in comments: this is COF (overflow approximation), not true academic OFI.

**Deliverables:**

- `hot_scanner.rs` (NEW): QI EWMA from true L1 quote updates; COF from overflow path (v24-FIX-7); neutral EWMA reset after overflow (v22-M2)
- `rotation_scanner.rs` (NEW): Gaussian-Gaussian TS; σ_noise = max(0.02, intraday_atr_14_pct × 1.5); σ_0 = max(0.05, intraday_atr_14_pct × 3.0)
- `universe_scanner.rs` (NEW): ADV filter, RVOL, 100-line budget

**Acceptance Tests (AT-41 to AT-62):**
- AT-41 through AT-60b: same as v23 (plus COF using BidSize/AskSize tick types)
- **AT-56c: intraday ATR: QQQ3.L intraday_atr_14_pct < atr_14_pct (overnight gap excluded); σ_0 lower than v23; TS allocates more lines to QQQ3.L vs v23 baseline**
- **AT-61: COF path verified: hot_scanner input during overflow uses bid_size_delta_sum not bid_vol_sum**
- **AT-62: COF zero-size → hot_scanner EWMA unchanged (neutral 0.5 ratio does not update EWMA directionally)**

**Gate**: 23 tests pass; intraday_atr_14_pct used for all TS noise params; COF from BidSize/AskSize confirmed; QI neutral-state resume verified

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 22.2h | **Depends on**: Phase 13
*(+0.2h: TWAP min slice floor)*

**v24 Amendment:**
- **TWAP min slice floor**: `slice_interval = max(alpha_halflife_ms, 100u64)` — prevent sub-100ms TWAP slicing that would starve the async reactor.

**Deliverables:** Same as v23 + min slice floor.

**Acceptance Tests:** AT-61 through AT-76; AT-75 (TWAP cancel on Chandelier hit); AT-76 (slice interval floor: alpha_halflife=0.5ms → slice_interval=100ms)

**Gate**: 16 tests pass; TWAP cancel on Chandelier verified; min slice floor verified

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 22h | **Depends on**: Phase 14
*(+1h: ξ uncapped + GpdInfiniteVariance + DCC-GARCH RwLock timeout)*

**v24 Amendments:**

- **EVT ξ uncapped (v24-FIX-5):** Remove ξ ≤ 0.5 cap. Let MLE estimate float. `if xi >= 1.0 { log GpdInfiniteVariance { xi_mle }; return CVaRExceeded; }`. Lower bound only: `xi = xi.max(-0.5)` (numerical stability; Weibull tails have negative ξ). ≥50 exceedances threshold (v23-FIX-3) unchanged.

- **DCC-GARCH RwLock timeout**: `tokio::sync::RwLock` with timeout for DCC-GARCH matrix access. If RwLock is poisoned (task panic with lock held): `tokio::time::timeout(Duration::from_millis(500), rwlock.read())` — if timeout → re-initialize DCC-GARCH matrix to identity (safe fallback) + log `DccGarchRwLockTimeout`.

**Deliverables:**

- `cvar_heat.rs` (NEW): Maillard gate; EVT POT GPD with ξ uncapped; GpdInfiniteVariance → CVaRExceeded; DCC-GARCH with timeout + re-init; CVaR-Kelly scaling; VIX blind spot

**Acceptance Tests (AT-76 to AT-101):**
- AT-76 through AT-93c: same as v23
- **AT-93d UPDATE**: ξ_mle=1.8 → GpdInfiniteVariance logged → CVaRExceeded returned; NO clamping; NO CVaR calculation with clamped ξ
- **AT-93e: ξ_mle=0.8 (valid ξ < 1.0): GPD CVaR formula runs normally; NO veto from GpdInfiniteVariance**
- **AT-93f: DCC-GARCH RwLock timeout: simulate lock held >500ms → DccGarchRwLockTimeout logged → identity matrix used → RiskGate not paralyzed**

**Gate**: 26 tests pass; ξ≥1.0 → CVaRExceeded confirmed (AT-93d); ξ=0.8 runs normally (AT-93e); DCC-GARCH timeout recovery (AT-93f); ≥50 exceedances threshold verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 30h | **Depends on**: Phase 15
*(+2h vs v23: per-exchange settlement lag, intraday ATR computation, Polygon timeout fallback, Polars optimize, shutil.move, session scoping, Yellow alert throttle)*

**v24 Amendments:**

- **Per-exchange settlement lag (v24-FIX-2):** EXCHANGE_TIMEZONE_MAP gains `settlement_lag_days` field: NYSE=1, NASDAQ=1; LSE=2, XETRA=2, TSE=2, KRX=2, ASX=2, HKEX=2. Ouroboros step 2 corp action veto: `veto_date = ex_date_local - settlement_lag_days (business days, from reqTradingHours calendar)`.

- **Intraday ATR in Ouroboros step 3 (v24-FIX-8):** Compute `intraday_atr_14 = mean(bar.high - bar.low, last 14 sessions)` per asset (excludes Open-vs-PrevClose gap). `intraday_atr_14_pct = intraday_atr_14 / mid_price`. Write to asset_volatility.json alongside atr_14_pct.

- **Polygon /dividends timeout fallback:** If Polygon step 2 request times out → retain PREVIOUS corp_action_blocklist.json → log `Polygon504Timeout { endpoint, elapsed_ms }` → proceed to step 3. Never write empty blocklist on timeout.

- **Polars .optimize():** Add `.optimize()` before `.collect()` in all Polars lazy evaluation chains in steps 3-5.

- **shutil.move cross-device:** Replace `os.replace()` with `shutil.move()` for all atomic writes. If cross-device error → `shutil.copy2()` + `os.remove(src)`. Ensures atomicity across tmpfs→EBS boundaries.

- **Session scoping in data_fetch.py (v24-FIX-10):** All `aiohttp.ClientSession`, `redis.asyncio.Redis`, and asyncio primitives created INSIDE `async def fetch_all_tickers()`. No module-level singletons. Each restart creates fresh connections.

- **Yellow tier alert throttle:** Ouroboros failure → Yellow alert sent; subsequent Yellow alerts suppressed for 4h. Prevents weekend spam.

**Deliverables:**

- `ouroboros/` EXTENDED — 10-step pipeline:
  1. **Data fetch** — Polygon.io; nightly cost basis clear; reqPositions resync; `exchange_times.json` loaded (reqTradingHours)
  2. **Corporate action blocklist** — EXCHANGE_TIMEZONE_MAP + per-exchange settlement_lag; veto_date = ex_date_local - lag_days; Polygon timeout → retain previous; atomic shutil.move write
  3. **Universe discovery** — 5,000+ tickers; 5-day median INTRADAY spread; **`intraday_atr_14_pct`** computed (High-Low); `intraday_spread_cache.json` with `generated_at` + `market_open_hours_since` helper; Polars .optimize().collect()
  4. **Feature engineering** — Polars LazyFrame + .optimize(); /dev/shm during processing
  5. **Scoring** — ASER; .optimize().collect()
  6. **Meta-label training** — Logistic Regression / LightGBM fallback
  7. **Chandelier calibration** — ATR, MAE/MFE profiling
  8. **Thompson Sampling update** — posteriors; `intraday_atr_14_pct` + `ts_prior_sigma_0 = max(0.05, intraday_atr_14_pct × 3.0)`; write asset_volatility.json
  9. **DCC-GARCH update** — cross-asset correlation; `asia_cross_tz.json`
  10. **PDF generation + artifact write + Telegram ALIVE** — active_state.wal prefix-header CRC32 write (v24-FIX-9); Yellow alert throttle

- All sessions/clients scoped to `fetch_all_tickers()` (v24-FIX-10)

**Acceptance Tests (AT-98 to AT-126):**
- AT-98 through AT-113b: same as v23
- **AT-111d: NYSE T+1: ex_date_local=2026-04-10 → settlement_lag=1 → veto_date=2026-04-09; AT-111c (HKEX T+2) unchanged**
- **AT-114: Polars .optimize(): verify via `.explain(optimized=True)` that projection pushdown is applied before .collect()**
- **AT-115: Polygon timeout: simulate 504 → previous blocklist retained → log Polygon504Timeout → step 3 continues**
- **AT-116: intraday_atr_14_pct: QQQ3.L intraday_atr_14_pct < atr_14_pct (High-Low < total ATR including gap); both fields in asset_volatility.json**
- **AT-117: shutil.move cross-device: simulate EXDEV error → copy2 + remove fallback; file arrives at destination atomically**
- **AT-118: Session scoping: data_fetch.py restart; no "attached to different loop" error; fresh session created in new thread**
- **AT-119: Yellow throttle: Ouroboros fails Friday → 1 Telegram alert sent; retry every hour → 0 additional alerts for next 4h**

**Gate**: 29 tests pass; per-exchange T+1/T+2 verified; intraday ATR in asset_volatility.json; Polygon timeout fallback verified; session scoping verified; Yellow alert throttle verified; shutil.move cross-device verified

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 15.5h | **Depends on**: Phase 16
*(+0.5h: Telegram keep-alive, 429 backoff, C-binding exception catch)*

**v24 Amendments:**
- **Telegram keep-alive 30s ping:** polling loop sends `getUpdates` every 30s even if no messages. If no response → detect silently severed connection → restart polling.
- **HTTP 429 exponential backoff:** on 429 response → wait `retry_after` seconds from response header (or 60s default) → retry. Backoff caps at 300s.
- **C-binding exception catch:** outer `while True:` in `telegram_reporter.py` catches ALL exceptions including C-level crashes in python-telegram-bot's asyncio C extensions. `except Exception` at outermost scope → log → sleep 5s → restart loop.

**Acceptance Tests (AT-119 to AT-132):**
- AT-119 through AT-130: same as v23
- **AT-131: Keep-alive: simulate 90s silence → polling detects dead connection → restarts within 35s**
- **AT-132: HTTP 429: inject 429 with Retry-After: 120 → bot waits 120s → resumes**

**Gate**: 17 tests pass; keep-alive verified; 429 backoff verified; C-binding outer catch verified

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 21.5h | **Depends on**: Phase 17
*(+0.5h: Nordic lit venue routing, TOML u32 serde)*

**v24 Amendments:**
- **Nordic lit venue routing:** OMX Stockholm equities: `route_to_lit = true` flag in exchange_profile.rs. SmartRouter forces lit venue (Nasdaq Stockholm, Oslo Bors) — no dark MTF routing. Comment: `// Nordic dark pools exhibit extreme adverse selection for non-HFT participants.`
- **TOML u32 explicit serde:** `transaction_tax.toml` uses u32 for bps fields. Explicit `#[serde(deserialize_with = "deserialize_u32_from_any")]` to handle TOML's default i64 serialization → prevents boot panic on standard TOML parsers.

**Acceptance Tests (AT-134 to AT-155):**
- AT-134 through AT-153: same as v23
- **AT-154: Nordic routing: OMX Stockholm equity → forced to lit venue; dark MTF route rejected**
- **AT-155: TOML u32 serde: load transaction_tax.toml with French FTT 20 bps → u32 parsed correctly from TOML i64; no panic**

**Gate**: 27 tests pass; Nordic lit venue routing verified; TOML u32 serde verified; 5 paper trading days

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21h | **Depends on**: Phase 18
*(unchanged from v23)*

**Gate**: 20 tests pass

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 24h | **Depends on**: Phase 19
*(unchanged from v23)*

**Gate**: 25 tests pass

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13.2h | **Depends on**: Phase 20
*(+0.2h: artifact freshness 26h → 96h)*

**v24 Amendment:**
- **Artifact freshness 96h:** `updated_at` check: `if now() - updated_at > 96h → ArtifactStale`. 96h covers weekend (Friday night artifact valid through Monday; 3-day bank holidays covered). Previous 26h caused false stale alerts every Monday.

**Acceptance Tests (AT-204 to AT-217):**
- AT-204 through AT-215: same as v23
- **AT-216: Artifact freshness: Friday 22:00 UTC artifact → Monday 09:00 UTC load (59h) → NOT stale (59h < 96h)**
- **AT-217: Artifact truly stale: Monday 22:00 UTC artifact → following Saturday 09:00 UTC (131h) → stale → ArtifactStale logged**

**Gate**: 17 tests pass; 96h freshness threshold verified; 5 paper trading days

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 36h | **Depends on**: Phase 21
*(+1.5h vs v23: prefix-header CRC32, Yellow timeout, WAL skip-corrupt, Prometheus type labels)*

**v24 Amendments:**

- **active_state.wal prefix-header format (v24-FIX-9):**
  - **Write**: (1) Serialize positions to JSON string. (2) Compute CRC32 of JSON string bytes. (3) Write to `active_state.wal.tmp`: first line = CRC32 hex string, second line = JSON. (4) `os::rename(".tmp", "active_state.wal")` — atomic on POSIX.
  - **Read**: (1) Read first line → CRC32 header. (2) Read remaining bytes → JSON string. (3) Recompute CRC32 of JSON string. (4) Compare: mismatch → `ActiveStateCorrupt` → WAL replay. Missing first line / not valid hex → `ActiveStateNoCrc32` → WAL replay.
  - **Advantage**: Torn write before JSON body → CRC32 header present, JSON invalid → serde_json error caught → WAL replay. Torn write during JSON body → CRC32 present, JSON incomplete → serde_json error → WAL replay. Torn write before CRC32 line written → empty file → `ActiveStateNoCrc32` → WAL replay. All torn-write scenarios handled BEFORE serde invoked.

- **WAL replay timeout → Yellow (v24-FIX-4):** `DrawdownTier::Yellow` on 30s timeout (not Orange). 7-day stale + fast-path fail → Yellow. Telegram: `"WAL replay timeout. Yellow (read-only) mode. No new entries. Manual RESUME required."` Orange is reserved for drawdown events only.

- **WAL replayer skip-corrupt:** On serde_json error mid-replay (corrupted WAL event): log `WalEventCorrupt { byte_offset, error }` → skip that event → continue replay of remaining events. Do not abort replay on single corrupted event.

- **Prometheus metric types:** All metrics explicitly typed: `# TYPE aegis_active_lines gauge`, `# TYPE aegis_trades_count counter`, etc. Required for standard Prometheus scraper compatibility.

**Deliverables:**

- **active_state.wal prefix-header atomic write (v24-FIX-9)**
- **WAL replay timeout → Yellow (v24-FIX-4)** with Telegram alert
- **WAL replayer skip-corrupt (WalEventCorrupt log)**
- **Prometheus typed metrics**
- All v23 deliverables retained (S3 backup cron, ArcSwap, PDF cleanup)

**Acceptance Tests (AT-216 to AT-238):**
- AT-216 through AT-229: same as v23 (renumbered for added tests)
- **AT-230: Prefix-header format: verify active_state.wal line 1 = valid CRC32 hex; line 2 = valid JSON; CRC32 of line 2 matches line 1**
- **AT-231: Torn write before JSON: file contains only CRC32 line → serde_json error on empty payload → `ActiveStateNoCrc32` → WAL replay**
- **AT-232: Torn write mid-JSON: file contains CRC32 + truncated JSON → serde_json parse error → `ActiveStateCorrupt` → WAL replay (CRC32 header safely read before parse attempted)**
- **AT-233: WAL replay timeout → Yellow: inject 10,000+ WAL events; replay times out at 30s → `DrawdownTier::Yellow` (NOT Orange); Telegram alert contains "Yellow"**
- **AT-234: 7-day stale WAL + fast-path fail → Yellow (not Orange)**
- **AT-235: WAL skip-corrupt: inject 5 corrupted events in 1000-event WAL → 5 WalEventCorrupt logs → remaining 995 events replayed correctly**
- **AT-236: Prometheus metric types: /metrics endpoint contains `# TYPE aegis_active_lines gauge` header**

**Gate**: 27 tests pass; prefix-header format verified end-to-end; timeout → Yellow (not Orange) verified; skip-corrupt verified; Prometheus types verified; 48h continuous paper run

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(unchanged from v23)*

**Suite 7 updated for v24:**
- Ouroboros completes all 10 steps with NYSE T+1 and LSE T+2 corp action veto dates verified
- Market-hours staleness guard: cache generated Friday → valid Monday (no ETP-only fallback)
- Watchdog thread confirmed (watchdog.log present; no SIGTERM triggered during 24h run)
- SemaphorePermitGuard: grep confirms zero mem::forget + zero add_permits in Drop (natural RAII only)
- active_state.wal: prefix-header format verified (line 1 = CRC32; line 2 = JSON)

**Gate**: All 7 suites pass. 100 validated paper trades. WR ≥ 40%. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ |
| **8** | Pre-Conditions + P0 (SC-01→SC-18-W + v24 amendments) | **52h** | **NEXT** | Unit tests per SC |
| **11** | Clock + SubscriptionManager (Relaxed, contractDetailsEnd, channel=64) | **22.5h** | NOT STARTED | AT-01→20 |
| **12** | Smart Router (market-hours staleness) + ISA Gate (const phf) | **22h** | NOT STARTED | AT-19→42 |
| **13** | HotScanner + RotationScanner (intraday ATR, COF from BidSize/AskSize) | **26h** | NOT STARTED | AT-41→62 |
| **14** | Infinite Chandelier + Executioner V2 (min slice floor 100ms) | **22.2h** | NOT STARTED | AT-61→76 |
| **15** | RiskGate 31 Vetoes + CVaR (ξ uncapped + GpdInfiniteVariance + DCC lock) | **22h** | NOT STARTED | AT-76→101 |
| **16** | Ouroboros (T+1/T+2, intraday ATR, Polygon fallback, session scope) | **30h** | NOT STARTED | AT-98→119 |
| **17** | Telemetry (keep-alive, 429 backoff, C-binding catch) | **15.5h** | NOT STARTED | AT-119→132 |
| **18** | European Equities (Nordic lit venue, TOML u32 serde) | **21.5h** | NOT STARTED | AT-134→155 (+5 paper days) |
| **19** | Asia-Pac MODE A | **21h** | NOT STARTED | AT-158→173 |
| **20** | Carry State Machine | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence (96h freshness) | **13.2h** | NOT STARTED | AT-204→217 (+5 paper days) |
| **22** | Institutional Hardening (prefix CRC32, Yellow timeout, skip-corrupt) | **36h** | NOT STARTED | AT-216→236 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~365h** | | **~262 acceptance tests** |

*(+11h vs v23: v24-FIX-1 SIGTERM watchdog +0.5h, v24-FIX-7 COF quote ticks +1h, v24-FIX-5 ξ uncapped +0.5h, v24-FIX-8 intraday ATR +1h, v24-FIX-9 prefix CRC32 +1h, v24-FIX-3 market-hours staleness +1h, v24-FIX-10 session scope +0.5h, v24-FIX-2 T+1/T+2 +0.5h, minor fixes +4.5h)*

**At 20h/week**: ~18.3 weeks to live capital
**At 40h/week**: ~9.1 weeks to live capital

---

### Infrastructure & Hardware Requirements

| Resource | Current | Required | When | Action |
|----------|---------|----------|------|--------|
| **RAM** | 4GB (c7i-flex.large) | 4GB sufficient for Phases 8-23 | Phase Q2+ | Upgrade to c7i.xlarge at Q2+ |
| **CPU** | 2 vCPU | 2 vCPU sufficient | Phase Q2+ | Upgrade at Q2+ |
| **EBS Storage** | Check current | **50GB minimum** | **NOW** | Expand if < 50GB. ~$4/mo |
| **GPU** | None | **None needed** through Phase 23 | Phase Q3+ DQN | No action |
| **Polygon.io** | Check tier | Stocks Starter ($29/mo) — needs `/v3/reference/dividends` + aggregates | **NOW** | Confirm or upgrade |
| **IBKR L1 real-time** | Paper (delayed) | Live trading: LSE + EU data subs ~$15/mo | At live capital stage | Subscribe when go-live |
| **IBKR L2** | None | Not needed until Phase Q2+ | Phase Q2+ | No action |
| **Bloomberg/Databento** | None | Not needed until Phase Q2+ | Phase Q2+ | No action |

**Immediate actions for user (before starting Phase 8)**:
1. ✅ Confirm EBS storage ≥ 50GB (`df -h` on EC2)
2. ✅ Confirm Polygon.io Stocks Starter or higher
3. ✅ Confirm `docker-compose.yml` has `restart: unless-stopped` on aegis-v2 service (watchdog SIGTERM → Docker restart)
4. No GPU, no extra RAM, no new data subscriptions needed for Phases 8-23

---

### New Files Created in Phases 8-23
*(v23 list + v24 additions)*

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — Relaxed AtomicUsize + SemaphorePermitGuard natural RAII + contractDetailsEnd
├── watchdog.rs                (Phase 8) — std::thread; libc::kill(SIGTERM); catch_unwind inner loop
├── mode_controller.rs         (Phase 11) — channel=64
├── smart_router.rs            (Phase 12) — market-hours staleness guard; phf const ISA hard-block
├── isa_gate.rs                (Phase 12) — phf::Set for blocked exchanges; April 6 boundary
├── hot_scanner.rs             (Phase 13) — COF from BidSize/AskSize delta; neutral EWMA on overflow
├── rotation_scanner.rs        (Phase 13) — intraday_atr_14_pct for σ_noise + σ_0
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — TWAP cancel + min slice floor 100ms
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — ξ uncapped; GpdInfiniteVariance; DCC-GARCH RwLock timeout
├── overnight_carry.rs         (Phase 20) — HashSet + UnauthorizedPnLStream
├── currency.rs                (Phase 18)
├── exchange_profile.rs        (Phase 18) — Nordic lit venue routing flag
├── transaction_tax.rs         (Phase 18) — TOML u32 explicit serde
├── sub_universe_allocator.rs  (Phase 18)
└── asian_exchange.rs          (Phase 19)

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — sessions scoped to function; no module-level singletons; shutil.move
├── ouroboros/symbology_mapper.py
├── telegram_reporter.py       (Phase 17) — keep-alive 30s ping; 429 backoff; C-binding outer catch
├── pdf_generator.py           (Phase 17)
├── shadow_book.py             (Phase 17)
├── cross_timezone.py          (Phase 21)
└── asia_universe.py           (Phase 21)

config/
├── european_exchange_profiles.toml  (Phase 18) — Nordic lit venue flag
├── european_routing_table.toml      (Phase 18)
├── transaction_tax.toml             (Phase 18) — u32 bps with explicit serde
├── asian_exchange_profiles.toml     (Phase 19)
└── asian_routing_table.toml         (Phase 19)

calibration/
├── weights.json               (Ouroboros step 10)
├── asia_cross_tz.json         (Ouroboros step 9)
├── corp_action_blocklist.json (Ouroboros step 2 — per-exchange T+1/T+2 veto_date)
├── intraday_spread_cache.json (Ouroboros step 3 — with generated_at + market_open_hours helper)
├── asset_volatility.json      (Ouroboros step 8 — atr_14_pct + intraday_atr_14_pct + ts_prior_sigma_0)
├── exchange_times.json        (Ouroboros step 1 — DST + trading hours; used for staleness guard)
├── active_state.wal           (Ouroboros step 10 — prefix-header: line1=CRC32hex, line2=JSON)
└── compaction_manifest.json   (Phase 22 — last_compaction_ts)

logs/
└── watchdog.log               (Phase 8 — std::fs; independent of WAL writer)
```

---

## TDD MANDATE (NON-NEGOTIABLE)

*(unchanged from v23)*

1. Write the test first (failing)
2. Write the implementation
3. Run `cargo test` — must pass before next SC item
4. Gate document MUST contain literal `cargo test` output

---

## TERMINAL KICKOFF PROMPT (Phase 8)

Paste into a new Claude Code terminal session to begin Phase 8:

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v24.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v24.md

IMPLEMENTATION TOOLING MANDATE: accept-edits mode ONLY. No bypass-permissions.
Ralph Wiggum stop hook retained. All bash commands require manual approval.

TDD MANDATE: test first (failing) → implement → cargo test (passing) → next SC.
Never batch. Never skip. Gate requires literal cargo test output.

Add to Cargo.toml: libc = "0.2"

SC-01: SIGTERM handler in main.rs.
  USE tokio::signal::ctrl_c() AND tokio::signal::unix::signal(SignalKind::terminate()).
  DO NOT use ctrlc crate (races with tokio — G2-IN12 mandate).
  On signal: flatten positions → wait 30s fills → WAL SystemShutdown → process::exit(0).

SC-01a: docker-compose.yml — stop_grace_period: 60s + restart: unless-stopped on aegis-v2.

SC-02: SubscriptionManager in subscription_manager.rs.
  MEMORY ORDERING: Ordering::Relaxed for ALL AtomicUsize ops (telemetry only).
  DO NOT use AcqRel or SeqCst. Semaphore(100) enforces budget.
  Add comment: // Semaphore enforces budget. AtomicUsize is telemetry only. Relaxed ordering.
  BUDGET: tokio::sync::Semaphore(100) for ≤100 line budget.
  PERMIT GUARD: SemaphorePermitGuard { _permit: OwnedSemaphorePermit }
    Use acquire_owned().await to get OwnedSemaphorePermit.
    Store as _permit field. Natural Drop returns permit to Semaphore automatically.
    DO NOT call mem::forget. DO NOT call add_permits. The _permit field Drop handles it.
  AT-18b: 1000 concurrent subscribe/cancel → active_line_count never > 100.
  AT-18c: tokio::spawn 100 tasks each acquiring guard then panic → available_permits()==100.
  AT-18d: grep confirms NO Relaxed→AcqRel/SeqCst upgrade; all Ordering::Relaxed.

SC-03: LineBudget {carry, active, scan} with assert!(carry+active+scan<=100).

SC-04: Two-tier data: IBKR token bucket 60/10min, max 6 concurrent, Error 162 backoff.
  Polygon.io for nightly 5000+ tickers. Separate Python token bucket in Ouroboros.

SC-05: MINIMUM_ENTRY_GBP: f64 = 1500.0 — suspended while validated_trades < 250.

SC-06: Dust guard — filled < £500.0 → Peg-to-Mid TIF=3min → if unfilled → market-sell.

SC-07: Remove V1 S3 reactivation comment from mean_reversion.py.

SC-08: APScheduler audit — all pre-LSE jobs timezone="Europe/London".

SC-09: crossbeam-channel bounded (cap=50000). On TrySendError::Full → DUAL PATH:
  (a) COF path (v24-FIX-7 — Cont et al. 2014 compliant):
    Accumulate bid_size_delta_sum from IBKR BidSize QUOTE tick type (NOT Last/LastSize trade).
    Accumulate ask_size_delta_sum from IBKR AskSize QUOTE tick type.
    ZERO-SIZE GUARD: if bid_size_delta_sum==0.0 AND ask_size_delta_sum==0.0 → emit ratio=0.5.
    Otherwise: COF = (bid_size_delta_sum - ask_size_delta_sum) /
                     (bid_size_delta_sum + ask_size_delta_sum + 1e-9)
    Emit WalPayload::QuoteImbalanceCompressed { ticker_id, bid_size_delta_sum,
                                                ask_size_delta_sum, dropped_count }
    Add code comment: // COF (Compressed Order Flow): approximation of OFI during overflow.
    // Uses BidSize/AskSize quote deltas per Cont et al. (2014). Not trade volume.
  (b) Chandelier path: bar.high=max(bar.high, tick.last); bar.low=min(bar.low, tick.last);
      bar.volume+=tick.volume.
  AT-60: 200 overflow BidSize/AskSize ticks → COF ratio matches manual ±0.001.
  AT-60b: 50 overflow ticks with bid_size_delta=0, ask_size_delta=0 → COF=0.5 (neutral).
  AT-61: hot_scanner input during overflow = bid_size_delta_sum (not bid_vol_sum).

SC-10: CostBasisEntry HashMap; nightly clear + reqPositions resync.

SC-11: AtomicUsize Relaxed; increment on reqMktData ACK (fetch_add Relaxed),
  decrement on cancelMktData ACK (fetch_sub Relaxed). NO reqOpenOrders.

SC-12: symbology_mapper.py — all 6 rules.

SC-13: kelly_scale ramp + POLARS_MAX_THREADS=2 + SplitAdjustment WAL.

SC-14: reqMarketDataType(3) — FIRST call in ibkr_broker.rs::connect().

SC-15: StrategyId::HotScanner + StrategyId::RotationScanner.

SC-16: shm_size: '2gb' in docker-compose.yml.

SC-17: WalPayload::QuoteImbalanceCompressed { ticker_id: TickerId,
  bid_size_delta_sum: f64, ask_size_delta_sum: f64, dropped_count: u32 }.

SC-18-W: Watchdog thread (v24-FIX-1 — CRITICAL: read this carefully):
  Declare at module root: static LAST_TICK_TS: AtomicU64 = AtomicU64::new(0);
  In engine.rs: call record_tick() = LAST_TICK_TS.store(unix_secs(), Ordering::Relaxed);
    on every received market data tick.
  In main.rs: std::thread::spawn(|| {
    loop {
      std::thread::sleep(Duration::from_secs(60));
      let result = std::panic::catch_unwind(|| {
        let last = LAST_TICK_TS.load(Ordering::Relaxed);
        let now = unix_secs();
        if is_market_hours() && (now - last) > 120 {
          eprintln!("[WATCHDOG] No tick in {}s during market hours.", now - last);
          let _ = std::fs::write("/app/logs/watchdog.log",
            format!("WATCHDOG TRIP {} elapsed={}s\n", now, now - last));
          // Send SIGTERM to self — triggers SC-01 graceful shutdown.
          // DO NOT call std::process::exit(1) — that bypasses Drop and WAL flush.
          unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
        }
      });
      if result.is_err() {
        eprintln!("[WATCHDOG] Inner loop panicked. Continuing outer loop.");
        let _ = std::fs::write("/app/logs/watchdog.log", "WATCHDOG INNER PANIC\n");
      }
    }
  });
  // The watchdog thread MUST NOT terminate. catch_unwind keeps it alive on inner panics.
  // Docker restart: unless-stopped restarts container after SIGTERM graceful exit.

After all 18 SC items + watchdog have passing tests:
- cargo test — paste LITERAL output
- docker build — must succeed
- docker-compose.yml: confirm stop_grace_period: 60s, POLARS_MAX_THREADS=2, shm_size: '2gb',
  restart: unless-stopped
- grep subscription_manager.rs: NO AcqRel, NO SeqCst, NO mem::forget, NO add_permits in Drop
- grep watchdog.rs: NO process::exit, ONLY libc::kill(SIGTERM)
- grep main.rs: NO ctrlc crate (tokio::signal only)
- AT-18c passes: 100 tokio::spawn panics → available_permits()==100
- AT-60b passes: zero-size overflow → COF=0.5
- Run 30-min paper session: watchdog.log NOT tripped during normal operation
- SIGTERM drill: kill container → WAL SystemShutdown event present → clean restart

Do NOT start Phase 11 until Phase 8 gate signed off with pasted cargo test output.
```

---

*AEGIS_MASTER_PLAN_v24.md — Generated 2026-03-09*
*Supersedes: AEGIS_MASTER_PLAN_v23.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v23.md (Gemini G5 "Institutional Syndicate" 200-bullet adversarial audit of v23)*
*10 G5-P priority fixes + 3 improvements + 14 minor operational fixes*
*Total acceptance tests: ~262 (vs ~248 in v23)*
*Total remaining hours: ~365h (vs ~354h in v23, +11h for v24 additions)*
