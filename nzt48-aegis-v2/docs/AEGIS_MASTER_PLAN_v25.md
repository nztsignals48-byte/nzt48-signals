# AEGIS V2 — MASTER PLAN v25
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 25.0 | **Date**: 2026-03-10 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v24. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v24.md — the Gemini G6 "Adversarial Operator" 200-bullet adversarial audit of v24. New fixes are marked **[v25-FIX-N]** for traceability. The G6 audit found 11 genuine priority fixes (G6-P1 through G6-P11), 3 improvements (G6-I1 through G6-I3), and 6 operational fixes (G6-O1 through G6-O6). The remaining ~170 bullets were duplicates of prior fixes, academic deferrals, or FUD.

---

## v25 DELTA — G6 PRIORITY FIXES

| Fix | G6 ID | Trap | What was wrong in v24 | What v25 does |
|-----|-------|------|-----------------------|---------------|
| **v25-FIX-1** | G6-P1 | Watchdog SIGTERM ignored by Docker PID 1 | `libc::kill(SIGTERM)` is ignored by PID 1 in Docker unless an explicit signal handler is installed. If Tokio is deadlocked (the exact scenario the watchdog targets), the Tokio SIGTERM handler cannot run. Process hangs forever. | After `libc::kill(SIGTERM)`, watchdog sleeps 5s then calls `libc::_exit(1)`. WAL flush cannot happen if Tokio is hung anyway — positions are already orphaned. Docker `restart: unless-stopped` restarts the container. |
| **v25-FIX-2** | G6-P2 | T+1/T+2 ignores bank holidays | `settlement_lag_days` arithmetic used calendar day subtraction. Ex-date day-after-Memorial-Day with NYSE T+1 → veto_date = Memorial Day (not a trading day). Corp action blocker fires on the wrong date. | Add `cal-date` Python library. Ouroboros step 2 uses `BusinessCalendar(exchange_name).subtract_business_days(ex_date, lag)`. Skips bank holidays per exchange calendar. |
| **v25-FIX-3** | G6-P4 | BufReader::read_line OOM on corrupted WAL CRC32 header | `read_line()` reads until `\n`. If torn write dropped the `\n` from the 9-byte CRC32 header, `read_line()` reads the entire file into RAM. On a large WAL → OOM during startup — the most critical phase. | Replace `read_line()` with `read_exact(&mut [0u8; 9])` for the CRC32 header. Fixed-width read: 8 hex chars + `\n`. If `UnexpectedEof` → `ActiveStateNoCrc32` → WAL replay. |
| **v25-FIX-4** | G6-P5 | aiohttp FD leak on Ouroboros thread kill | `async with aiohttp.ClientSession()` context manager only cleans up if the coroutine reaches the `async with` block. Exception before entry → no cleanup → FD leak. Repeated nightly restarts exhaust `ulimit -n 1024` within weeks on EC2. | Explicit `try/finally` in `fetch_all_tickers()`: session created before try, `await session.close()` + `await session.connector.close()` in finally block. Always runs even on exception or timeout. |
| **v25-FIX-5** | G6-P6 | Intraday ATR excludes gaps → over-allocates to gap-prone ETPs | v24's `intraday_atr_14_pct = mean(H-L)` understates risk for 3x ETPs that gap overnight but have narrow intraday ranges. Thompson Sampler sees low noise → allocates more scanner lines than warranted. | Hybrid intraday ATR: `hybrid_range = max(H-L, gap_magnitude × 0.6)` where `gap_magnitude = abs(open - prev_close)`. 0.6 multiplier = conservative gap bleed factor. Written to `asset_volatility.json` as `hybrid_intraday_atr_14_pct`. All TS noise params use hybrid value. |
| **v25-FIX-6** | G6-P7 | OwnedSemaphorePermit drops without sending cancelMktData | When a `tokio::select!` branch is cancelled, `SemaphorePermitGuard` drops correctly (permit returned to Semaphore) but the IBKR `reqMktData` subscription is still active. Live subscription against a dead slot — IBKR eventually issues Error 200 (no security definition). | `SemaphorePermitGuard` gains `ticker_id: TickerId` + `cancel_tx: mpsc::Sender<CancelMktDataCmd>` fields. `Drop` calls `cancel_tx.try_send(...)` (non-blocking). Background IBKR actor drains the cancel queue and sends `cancelMktData`. |
| **v25-FIX-7** | G6-P8 | COF accumulation ignores bid/ask directionality | SC-09 summed `bid_size_delta_sum` from all BidSize ticks without tracking whether each tick was an improvement or deterioration. BBO refresh (bid refreshes to same level) was treated as bullish pressure — signal direction inverted. | COF accumulator tracks `prev_bid_size` and `prev_ask_size` per ticker. Delta = `new_size - prev_size`. Positive bid delta → bullish (add to bid_sum). Negative bid delta → bearish (add to ask_sum). Mirror for AskSize. Directionally correct per-tick while still aggregating over overflow window. |
| **v25-FIX-8** | G6-P9 | shm_size:2gb swap risk — no pre-flight check | `/dev/shm` backed by RAM. If EC2 has insufficient free RAM, kernel swaps `/dev/shm` → Polars operations on swapped memory are catastrophically slow. No guard existed. | Ouroboros startup: `psutil.virtual_memory().available ≥ 2.5GB` check. Failure → `RuntimeError` → Ouroboros aborts → Yellow alert (positions unaffected). |
| **v25-FIX-9** | G6-P10 | Yellow throttle hides boundary oscillation | v24 suppressed repeated Yellow alerts for 4h. If engine flaps Yellow/normal repeatedly, operator receives 1 alert then silence. | Dual-track: (1) per-alert 4h suppression (unchanged); (2) hourly summary to Telegram reports count of suppressed events in the hour. Summary is never throttled. |
| **v25-FIX-10** | G6-P11 | WAL file size unlimited → OOM on startup | No file size guard before reading `active_state.wal`. A compaction failure over 7 days could grow WAL to GB. `BufReader` read → OOM during startup. | Pre-read guard: `metadata.len() > 100MB → DrawdownTier::Yellow` (do not attempt read). Telegram alert: "WAL oversized — manual review required." |
| **v25-FIX-11** | G6-I3 | Watchdog `is_market_hours()` shares clock library dependency | Watchdog is the last-resort deadlock detector. It calls `is_market_hours()` from `clock.rs` — the very module it is protecting. DST bug in `clock.rs` could cause watchdog to fire during off-hours or suppress during market hours. | Watchdog uses raw UTC hour arithmetic: `let utc_hour = (now % 86400) / 3600; let in_window = utc_hour >= 7 && utc_hour < 18;`. Conservative (covers BST+GMT). Zero dependency on `clock.rs`. |

**v25-MINOR-FIXES** (operational):
- **Business-day calendar**: `cal-date` Python library for settlement lag (G6-P2)
- **Half-day staleness**: SmartRouter uses actual_trading_hours_since (sum of open minutes) for staleness calculation, not binary open/closed (G6-I1)
- **TWAP token bucket wiring**: slice interval wired to order rate limiter (G6-I2)
- **Watchdog serial tests**: `#[serial_test::serial]` on LAST_TICK_TS tests to prevent global state conflicts (G6-O1)
- **Schema versioning**: `schema_version: N` field in all calibration JSON outputs (G6-O2)
- **Prometheus localhost-only**: metrics endpoint bound to `127.0.0.1:9090` (G6-O3)
- **compaction_manifest CRC32**: prefix-header format applied to compaction_manifest.json (G6-O4)
- **exchange_times.json 7-day TTL**: document TTL explicitly in Ouroboros step 1 (G6-O5)
- **transaction_tax hot-reload**: ArcSwap config reload path extended to transaction_tax.toml (G6-O6)

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

*(unchanged from v24)*

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

**V1 Critical Bugs** (unchanged from v24 — fix in V1 separately):
- TwelveData credit burnout: 3,176 vs 800/day limit. Add `max_calls_per_day` counter in realtime_data.py.

---

### 1.2 V2 Rust Engine — Complete Module Inventory

**Status: Phases 1-7 COMPLETE. ~9,000 LOC. 147+ tests.**

*(unchanged from v24)*

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
| P0-16 | Watchdog SIGTERM ignored by PID 1 | libc::_exit(1) fallback after 5s grace | **v25-FIX-1, Phase 8** |

**P1 — High:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | EOD spread cache + weekend stale | Intraday cache + market-hours staleness guard | **v22-FIX-2 + v24-FIX-3, Phase 12** |
| P1-2 | Telegram polling dies silently | Infinite retry + keep-alive + C-binding catch | **Phase 17** |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | QI suspension at peak alpha | COF aggregator (directional BidSize/AskSize delta) | **v22-FIX-3 + v24-FIX-7 + v25-FIX-7, Phase 8** |
| P1-6 | σ_noise 30-day lag + overnight gap | hybrid_intraday_atr_14_pct (H-L + gap bleed 0.6) | **v22-FIX-10 + v24-FIX-8 + v25-FIX-5, Phase 13** |
| P1-7 | Corp action timezone + settlement lag | EXCHANGE_TIMEZONE_MAP + business-day T+1/T+2 | **v22-FIX-7 + v24-FIX-2 + v25-FIX-2, Phase 16** |
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
| P1-20 | Semaphore permit leak + mem::forget | SemaphorePermitGuard natural RAII + cancelMktData on Drop | **v22-FIX-5 + v24-FIX-6 + v25-FIX-6, Phase 8** |
| P1-21 | active_state.wal non-atomic write | Prefix-header CRC32 + tmp + rename + read_exact | **v22-FIX-4 + v24-FIX-9 + v25-FIX-3, Phase 22** |
| P1-22 | WAL replay timeout → Orange liquidation | Timeout → Yellow; size guard 100MB | **v24-FIX-4 + v25-FIX-10, Phase 22** |
| P1-23 | Thompson Sampler σ_0 overnight gap bias | hybrid_intraday_atr_14_pct × 3.0 for σ_0 | **v24-FIX-8 + v25-FIX-5, Phase 13** |
| P1-24 | OFI calculated from trade volume | BidSize/AskSize directional delta (COF) | **v24-FIX-7 + v25-FIX-7, Phase 8** |
| P1-25 | T+2 hardcoded: US is T+1 since May 2024 | Per-exchange business-day settlement_lag | **v24-FIX-2 + v25-FIX-2, Phase 16** |
| P1-26 | T+1/T+2 ignores bank holidays | cal-date BusinessCalendar per exchange | **v25-FIX-2, Phase 16** |
| P1-27 | BufReader::read_line OOM on torn CRC32 header | read_exact 9 bytes for fixed-width header | **v25-FIX-3, Phase 22** |
| P1-28 | Watchdog SIGTERM ignored by Docker PID 1 | _exit(1) fallback after 5s SIGTERM grace | **v25-FIX-1, Phase 8** |
| P1-29 | aiohttp FD leak on thread restart | try/finally explicit session.close() | **v25-FIX-4, Phase 16** |
| P1-30 | COF accumulation ignores directionality | prev_bid/ask tracking for directional delta | **v25-FIX-7, Phase 8** |
| P1-31 | OwnedSemaphorePermit drops without cancelMktData | cancel_tx in SemaphorePermitGuard Drop | **v25-FIX-6, Phase 8** |

---

### 2.2 Binding Architectural Mandates (all versions + v25)

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Polars LazyFrame + Arrow zero-copy. .optimize() before .collect(). | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** crossbeam-channel (cap=50,000). Overflow → COF aggregator (directional BidSize/AskSize delta; zero-size → 0.5 neutral). **(v24-FIX-7 + v25-FIX-7)** | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox.** Token bucket for active ~100 tickers; Polygon for nightly universe. | Phase 8+16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed only when live position exists. | Phase 11 |
| **GEM-A5** | **Drawdown tiers.** Yellow (new entries blocked) / Orange (close all) / Red (full halt). WAL replay timeout → Yellow. WAL oversized → Yellow. **(v25-FIX-10)** | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time via Europe::London. | Phase 11 |
| **v20-A2** | **AtomicUsize(Ordering::Relaxed) for telemetry.** Semaphore(100) is enforcement. SemaphorePermitGuard: natural RAII + cancelMktData on Drop. **(v24-FIX-6 + v25-FIX-6)** | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler.** σ_noise = max(0.02, hybrid_intraday_atr_14_pct × 1.5). σ_0 = max(0.05, hybrid_intraday_atr_14_pct × 3.0). **(v24-FIX-8 + v25-FIX-5)** | Phase 13 |
| **v20-A4** | **Account-level reqPnL + CarryMonitor whitelist + UnauthorizedPnLStream alert.** | Phase 20 |
| **v21-A1** | **No reqOpenOrders.** Internal AtomicUsize only. | Phase 11 |
| **v21-A2** | **shm_size: '2gb'.** Pre-flight RAM check ≥2.5GB available. **(v25-FIX-8)** | Phase 8/16 |
| **v21-A3** | **Maillard CF gate + EVT POT GPD.** ξ uncapped; ξ≥1 → GpdInfiniteVariance → CVaRExceeded. **(v24-FIX-5)** | Phase 15 |
| **v21-A4** | **COF aggregator on overflow.** Directional BidSize/AskSize delta (not trade volume). Zero-size → 0.5. **(v24-FIX-7 + v25-FIX-7)** | Phase 8 |
| **v21-A5** | **active_state.wal prefix-header format.** Line 1 = CRC32 hex (read via read_exact 9 bytes). Line 2+ = JSON. CRC before parse. **(v24-FIX-9 + v25-FIX-3)** | Phase 22 |
| **v22-A1** | **EXCHANGE_TIMEZONE_MAP + per-exchange business-day settlement_lag.** NYSE/NASDAQ=T+1; EU/Asia=T+2. Bank holidays via cal-date. **(v24-FIX-2 + v25-FIX-2)** | Phase 16 |
| **v22-A2** | **intraday_spread_cache.json + actual_trading_hours_since staleness guard.** **(v24-FIX-3 + G6-I1)** | Phase 12/16 |
| **v22-A3** | **accept-edits ONLY.** No bypass-permissions. | Process |
| **v23-A1** | **std::thread watchdog.** Stale >120s → self-SIGTERM via libc::kill → 5s grace → libc::_exit(1). Clock-independent UTC hour check. **(v24-FIX-1 + v25-FIX-1 + v25-FIX-11)** | Phase 8 |
| **v23-A2** | **WAL replay timeout 30s → Yellow (not Orange).** WAL oversized >100MB → Yellow. **(v24-FIX-4 + v25-FIX-10)** | Phase 22 |
| **v24-A1** | **Hybrid intraday ATR.** `hybrid_intraday_atr_14_pct = mean(max(H-L, gap_magnitude × 0.6))`. Excludes raw overnight gap but acknowledges gap-prone ETPs. **(v25-FIX-5 refines v24-A1)** | Phase 13/16 |
| **v24-A2** | **COF not OFI during overflow.** Directional BidSize/AskSize deltas. True OFI requires continuous L1 stream without overflow. **(v25-FIX-7)** | Phase 8 |
| **v25-A1** | **SemaphorePermitGuard sends cancelMktData on Drop.** Background actor handles IBKR call. Prevents live subscriptions on dead slots. **(v25-FIX-6)** | Phase 8 |
| **v25-A2** | **Ouroboros pre-flight RAM guard.** psutil check ≥2.5GB before Polars ops. **(v25-FIX-8)** | Phase 16 |
| **v25-A3** | **Business-day settlement arithmetic.** cal-date Python library for all exchange settlement lag calculations. Never naked calendar arithmetic. **(v25-FIX-2)** | Phase 16 |
| **v25-A4** | **Schema version in all calibration JSON.** `schema_version: N` field. Mismatch → treat as stale → regenerate. **(G6-O2)** | Phase 16 |
| **v25-A5** | **Watchdog clock-independent.** Raw UTC hour arithmetic `(unix_ts % 86400) / 3600`. Never calls clock.rs. **(v25-FIX-11)** | Phase 8 |

---

### 2.3 Deferred (Post-Crucible)

*(v24 defer table + v25 additions)*

| Finding | Reason |
|---------|--------|
| All prior deferred items | Unchanged from v24 |
| Full L2 order book for true OFI | IBKR L2 subscription needed; Phase Q2+ |
| Neural Hawkes process for tick arrival | Phase Q3-Q4 Quantum Apex |
| DQN reinforcement learning for execution | Phase Q3-Q4 Quantum Apex |
| Rust DPDK network stack | Phase Q3-Q4 Quantum Apex |
| VIX futures as CBOE feed fallback | Phase Q2+ |
| Post-trade TCA (Transaction Cost Analysis) | Phase Q2+ analytics |
| Smart order routing across IBKR + LSE Direct | Phase Q2+ infrastructure |
| Microstructure alpha decay measurement | Phase Q2+ |

---

## PART 3 — PHASE PLAN

### Numbering Convention
- **Phases 1-7**: COMPLETE
- **Phase 8**: Next — **19 SC items** (updated for v25)
- **Phases 11-23**: Granular build

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 56h | **Status**: NEXT
*(+4h vs v24: v25-FIX-1 _exit fallback +0.5h, v25-FIX-6 cancelMktData in Drop +2h, v25-FIX-7 COF directionality +1h, v25-FIX-11 UTC clock-independent +0.2h, serial test annotations +0.3h)*

**Rationale**: All v24 SC items retained. v25 amendments: SC-18-W watchdog adds `_exit(1)` fallback (v25-FIX-1) and replaces `is_market_hours()` with UTC arithmetic (v25-FIX-11); SC-02 SemaphorePermitGuard gains `cancel_tx` for cancelMktData on Drop (v25-FIX-6); SC-09 COF accumulator tracks directional deltas (v25-FIX-7).

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: `tokio::signal::ctrl_c()` + `tokio::signal::unix::signal(SignalKind::terminate())`. DO NOT use ctrlc crate (G2-IN12). Flatten → 30s wait → WAL SystemShutdown → exit. | main.rs | v23-FIX-8 |
| **SC-01a** | `stop_grace_period: 60s` in docker-compose.yml | docker-compose.yml | v20-FIX-1 |
| **SC-02** | SubscriptionManager skeleton. `AtomicUsize` for `active_line_count` with **`Ordering::Relaxed`** for ALL ops. Semaphore(100) enforces budget. **`SemaphorePermitGuard { _permit: OwnedSemaphorePermit, ticker_id: TickerId, cancel_tx: mpsc::Sender<CancelMktDataCmd> }`**. Natural Drop: (1) `cancel_tx.try_send(CancelMktDataCmd { ticker_id })` — non-blocking, infallible; (2) `_permit` drops automatically → permit returned to Semaphore via tokio RAII. DO NOT call `mem::forget`. DO NOT call `add_permits`. **(v25-FIX-6)**. Unit test AT-18b: 1000 concurrent subscribe/cancel → active_line_count never > 100. AT-18c: 100 tasks acquire guard then panic → permits==100 after. AT-18d: grep for AcqRel/SeqCst → zero. AT-18f: 10 guards dropped via select! cancellation → 10 CancelMktDataCmd in background actor queue → permits==100. | subscription_manager.rs | v24-FIX-6 + v25-FIX-6 |
| SC-03 | LineBudget `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket 60/10min for real-time ticks; Polygon.io Starter (confirmed) for nightly 5000+ ticker universe scan. Polygon dynamic token bucket: 4 req/min (below 5 req/min Starter limit), unlimited daily — no credit cap. `/v2/aggs` + `/v3/reference/dividends` + `/v3/reference/tickers` all confirmed live. | ibkr_broker.rs + data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` — suspended while validated_trades < 250 | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard: filled < £500.0 → Peg-to-Mid TIF=3min → if unfilled → market-sell | exit_engine.rs | v19-FIX-1 |
| SC-07 | Remove V1 S3 reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| **SC-09** | crossbeam-channel (cap=50,000). On TrySendError::Full → DUAL PATH: **(a) COF path (v25-FIX-7 — directional):** Track `prev_bid_size: f64` and `prev_ask_size: f64` per ticker in COF accumulator state. On each overflow BidSize tick: `let delta = new_bid_size - prev_bid_size; if delta > 0.0 { bid_size_delta_sum += delta; } else { ask_size_delta_sum += delta.abs(); }; prev_bid_size = new_bid_size;`. Mirror for AskSize. Zero-size guard: if bid_size_delta_sum==0 AND ask_size_delta_sum==0 → emit ratio=0.5 (neutral). COF = (bid_size_delta_sum − ask_size_delta_sum) / (bid_size_delta_sum + ask_size_delta_sum + 1e-9). Emit WalPayload::QuoteImbalanceCompressed. Comment: `// COF: directional BidSize/AskSize delta per Cont et al. (2014). Overflow approximation.` **(b) Chandelier path:** bar.high/low/volume update. Unit test AT-60: directional COF: BidSize 100→110→95→110 → bid_delta_sum=10+15=25, ask_delta_sum=15 (size decrease treated as ask pressure) → COF=(25-15)/41≈0.244. AT-60b: zero-size → COF=0.5. AT-60c: pure BidSize increases only → COF>0.5 (bullish). | python_bridge.rs + channel.rs + types/wal.rs | v24-FIX-7 + v25-FIX-7 |
| SC-10 | CostBasisEntry HashMap; nightly clear + reqPositions resync | portfolio.rs | G-09 |
| SC-11 | AtomicUsize Relaxed tracking; no reqOpenOrders | subscription_manager.rs | v21-FIX-2 |
| SC-12 | symbology_mapper.py — all 6 rules including reverse split | ouroboros/symbology_mapper.py | v19-FIX-2 |
| SC-13 | kelly_scale ramp + POLARS_MAX_THREADS=2 + SplitAdjustment WAL | risk_arbiter.rs + docker-compose.yml | v20-FIX-3 |
| SC-14 | reqMarketDataType(3) as FIRST call in connect() | ibkr_broker.rs | v20-FIX-8 |
| SC-15 | StrategyId::HotScanner + StrategyId::RotationScanner | types/enums.rs | v20-FIX-10 |
| SC-16 | shm_size: '2gb' in docker-compose.yml | docker-compose.yml | v21-FIX-5 |
| SC-17 | WalPayload::QuoteImbalanceCompressed with `bid_size_delta_sum: f64, ask_size_delta_sum: f64, dropped_count: u32` | types/wal.rs | v24-FIX-7 |
| **SC-18-W** | **Watchdog thread (v25-FIX-1 + v25-FIX-11):** `std::thread::spawn`. Track `AtomicU64 LAST_TICK_TS` (Ordering::Relaxed). Inner loop in `catch_unwind`. **UTC clock-independent market hours check (NO clock.rs)**: `let utc_hour = (now % 86400) / 3600; let in_window = utc_hour >= 7 && utc_hour < 18;` (covers BST+GMT). If in_window AND stale >120s: write watchdog.log → `unsafe { libc::kill(libc::getpid(), libc::SIGTERM) }` → `std::thread::sleep(Duration::from_secs(5))` → **`unsafe { libc::_exit(1) }`** (PID 1 SIGTERM fallback — Docker restart: unless-stopped handles recovery). **(v25-FIX-1: _exit fallback; v25-FIX-11: UTC arithmetic)**. Tests: `#[serial_test::serial]` on all tests that modify LAST_TICK_TS **(G6-O1)**. AT-18e: simulate SIGTERM handler NOT registered; inject 120s stale + in_window; verify process exits within 70s (65s watch sleep + 5s grace). | watchdog.rs + Cargo.toml | v24-FIX-1 + v25-FIX-1 + v25-FIX-11 |
| SC-19 | `contractDetailsEnd` handler in subscription_manager.rs — marks batch complete on IBKR callback | subscription_manager.rs | v24-minor |

**Gate**: All 19 SC items + tests pass; `cargo test` output pasted; grep subscription_manager.rs: NO AcqRel/SeqCst (all Relaxed), NO mem::forget, NO add_permits; grep watchdog.rs: NO process::exit(1), NO is_market_hours() call, UTC arithmetic present, _exit(1) present after SIGTERM; AT-18f passes (cancelMktData on Drop); AT-60c passes (directional COF); AT-18e passes (PID 1 _exit fallback); watchdog.log created on SIGTERM test

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 22.5h | **Depends on**: Phase 8
*(unchanged from v24)*

**v25 Note**: SemaphorePermitGuard extended to include `cancel_tx` field (SC-02). Phase 11 wires the CancelMktData background actor to the IBKR connection thread. The actor receives `CancelMktDataCmd` from guard Drops and serializes `cancelMktData(ticker_id)` calls on the IBKR socket.

**Deliverables:**
- `clock.rs` REWRITTEN — chrono-tz; `now_london()`, TradingMode enum, DST-correct boundaries
- `subscription_manager.rs` — Relaxed AtomicUsize; SemaphorePermitGuard with cancel_tx; contractDetailsEnd handler; proptest
- `mode_controller.rs` — capacity=64
- `cancel_mktdata_actor.rs` (NEW) — mpsc receiver; serializes cancelMktData on IBKR connection; bounded queue (1000 capacity)
- NZX pre-subscribe at 22:55 UTC

**Acceptance Tests:**
- AT-01 through AT-18f (v25: adds AT-18f cancelMktData integration)
- **AT-19: contractDetailsEnd handler verified**
- **AT-20: mode_controller 60 rapid transitions → no sender block**

**Gate**: 22 tests pass; cancel_mktdata_actor drains queue correctly; no reqOpenOrders; natural RAII Drop confirmed

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 22.5h | **Depends on**: Phase 11
*(+0.5h vs v24: actual_trading_hours_since calculation for half-days)*

**v25 Amendments:**
- **actual_trading_hours_since staleness (G6-I1):** Replace binary "was exchange open since generated_at" with `actual_trading_hours_since = sum of open minutes in interval`. `stale = (actual_trading_hours_since > 72 * 60)`. Half-day closes (Christmas Eve 12:30) count only their actual open minutes. No false-stale on half-days.

**Deliverables:**
- `smart_router.rs` — actual_trading_hours_since staleness; phf const ISA hard-block
- `isa_gate.rs` — April 6 boundary; phf::Set

**Acceptance Tests:**
- AT-37c: Friday 21:00 UTC cache → Monday 08:00 UTC load → NOT stale (no open minutes in interval)
- AT-37d: 72+ actual trading hours since generated_at → stale → ETP fallback
- **AT-37e (NEW): Christmas Eve (close 12:30) → only 210 open minutes counted → NOT stale vs full day**
- AT-41: ISA hard-block const phf verified

**Gate**: 26 tests pass; actual_trading_hours_since verified on half-day; Christmas Eve AT-37e passes

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 26.5h | **Depends on**: Phase 12
*(+0.5h vs v24: hybrid intraday ATR formula)*

**v25 Amendments:**
- **Hybrid intraday ATR (v25-FIX-5):** `hybrid_intraday_range = max(bar.high - bar.low, abs(bar.open - prev_bar.close) × 0.6)`. `hybrid_intraday_atr_14_pct = mean(hybrid_intraday_range[-14:]) / mid_price`. All TS noise params: σ_noise = max(0.02, hybrid_intraday_atr_14_pct × 1.5). σ_0 = max(0.05, hybrid_intraday_atr_14_pct × 3.0). Written to asset_volatility.json as `hybrid_intraday_atr_14_pct` alongside `intraday_atr_14_pct` (H-L only) for comparison.
- **COF input from directional accumulator (v25-FIX-7):** hot_scanner.rs receives COF ratio from QuoteImbalanceCompressed. COF is now directionally correct.

**Deliverables:**
- `hot_scanner.rs` — QI EWMA; directional COF from overflow; neutral EWMA on zero-size
- `rotation_scanner.rs` — Gaussian-Gaussian TS; hybrid_intraday_atr_14_pct for σ_noise + σ_0
- `universe_scanner.rs` — ADV filter, RVOL, 100-line budget

**Acceptance Tests:**
- AT-56c: QQQ3.L: hybrid_intraday_atr_14_pct ≥ intraday_atr_14_pct (gap bleed raises floor)
- **AT-56d (NEW): QQQ3.L with known 2% overnight gap → hybrid_intraday > pure_intraday → TS allocates fewer lines vs v24 baseline**
- AT-60c: directional COF BidSize increases → COF>0.5 (bullish)

**Gate**: 24 tests pass; hybrid ATR used for all TS noise params; directional COF verified

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 22.5h | **Depends on**: Phase 13
*(+0.3h vs v24: TWAP slice interval wired to token bucket)*

**v25 Amendment:**
- **TWAP token bucket wiring (G6-I2):** `slice_interval = max(alpha_halflife_ms, order_rate_limiter.next_available_slot_ms(), 100u64)`. Prevents concurrent TWAP orders from collectively breaching IBKR 60 orders/10min pacing limit.

**Gate**: 17 tests pass; TWAP slice interval respects token bucket; min 100ms floor verified

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 22h | **Depends on**: Phase 14
*(unchanged from v24)*

**Gate**: 26 tests pass; ξ≥1.0 → CVaRExceeded (AT-93d); ξ=0.8 normal (AT-93e); DCC-GARCH timeout recovery (AT-93f); ≥50 exceedances verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 34h | **Depends on**: Phase 15
*(+4h vs v24: cal-date business-day settlement +1.5h, hybrid intraday ATR +1h, FD cleanup try/finally +0.5h, RAM pre-flight +0.5h, hourly alert summary +1h, schema versioning +0.3h, other minor items +0.2h)*

**v25 Amendments:**

- **Business-day settlement lag (v25-FIX-2):** Install `cal-date` Python library (`pip install cal-date`). Ouroboros step 2:
  ```python
  from cal_date import BusinessCalendar
  exchange_cal_map = {
      'NYSE': 'NYSE', 'NASDAQ': 'NASDAQ', 'LSE': 'LSE',
      'XETRA': 'XETRA', 'TSE': 'TSE', 'KRX': 'KRX',
      'ASX': 'ASX', 'HKEX': 'HKEX'
  }
  cal = BusinessCalendar(exchange_cal_map[exchange])
  veto_date = cal.subtract_business_days(ex_date_local, settlement_lag_days)
  ```
  Never use `timedelta(days=lag)` arithmetic.

- **Hybrid intraday ATR (v25-FIX-5):** Ouroboros step 3 computes both `intraday_atr_14_pct` (H-L) and `hybrid_intraday_atr_14_pct` (max of H-L and gap_magnitude×0.6). Both written to `asset_volatility.json`. Includes `gap_bleed_factor: 0.6` for calibration tracking.

- **FD cleanup try/finally (v25-FIX-4):** `fetch_all_tickers` uses explicit try/finally for all session/client creation. No context manager relies on reaching `async with` block — explicit `await session.close()` + `await session.connector.close()` in finally.

- **RAM pre-flight check (v25-FIX-8):** `preflight_ram_check()` called at start of `run_ouroboros()`. Requires ≥2.5GB available. Failure → Yellow alert (not Orange).

- **Hourly suppressed-event summary (v25-FIX-9):** telegram_reporter tracks suppressed event counts per hour. Sends hourly summary if any events were suppressed. Summary format: `[AEGIS HOURLY] 14:00-15:00 UTC | Yellow entries: 3 (2 throttled) | Orange: 0 | Watchdog: 0`. Summary is never throttled.

- **Schema version (G6-O2):** All calibration JSON outputs include `"schema_version": 1` (or current version). Reader checks version: mismatch → treat as stale.

- **exchange_times.json 7-day TTL (G6-O5):** TTL documented in step 1. Only re-request if `(now - exchange_times_generated_at) > 7 days`.

**Deliverables:**
- `ouroboros/` EXTENDED — 10-step pipeline with all v25 amendments
- `cal-date` added to requirements.txt
- `psutil` confirmed in requirements.txt

**Acceptance Tests:**
- AT-111e (NEW): NYSE T+1 + Memorial Day holiday → veto_date = Friday before, NOT bank holiday itself
- AT-111f (NEW): LSE T+2 + Good Friday + Easter Monday → veto_date = Thursday (2 business days before Easter Monday)
- AT-116b (NEW): hybrid_intraday_atr_14_pct > intraday_atr_14_pct when gap > 0; gap_bleed_factor=0.6 in output JSON
- AT-118b (NEW): FD count does not increase after 100 simulated Ouroboros restart cycles with injected exception
- AT-119b (NEW): psutil.virtual_memory().available=1.5GB → RuntimeError → Ouroboros aborts → Yellow alert → Orange not triggered
- AT-119c (NEW): 5 Yellow alerts in 2h → 1 delivered, 4 suppressed → hourly summary reports count=5
- AT-120 (NEW): schema_version field present in all calibration JSON outputs
- All prior v24 ATs (AT-111d, AT-114 through AT-119) retained

**Gate**: 37 tests pass; business-day settlement verified on bank holidays; hybrid ATR in JSON; FD count stable; RAM check; hourly summary; schema_version in all outputs

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 15.5h | **Depends on**: Phase 16
*(unchanged from v24)*

**Gate**: 17 tests pass; keep-alive verified; 429 backoff; C-binding outer catch; hourly summary from Phase 16 integration

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 21.5h | **Depends on**: Phase 17
*(unchanged from v24)*

**Gate**: 27 tests pass; Nordic lit venue; TOML u32 serde; 5 paper trading days

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21h | **Depends on**: Phase 18
*(unchanged from v24)*

**Gate**: 20 tests pass

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 24h | **Depends on**: Phase 19
*(unchanged from v24)*

**Gate**: 25 tests pass

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13.2h | **Depends on**: Phase 20
*(unchanged from v24)*

**Gate**: 17 tests pass; 96h freshness verified; 5 paper trading days

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 39.5h | **Depends on**: Phase 21
*(+3.5h vs v24: read_exact CRC32 header +0.5h, WAL size guard +0.5h, compaction manifest CRC32 +0.3h, Prometheus localhost bind +0.1h, transaction_tax hot-reload +1h, new ATs +1.1h)*

**v25 Amendments:**

- **active_state.wal read_exact header (v25-FIX-3):** Replace `BufReader::read_line()` with `reader.read_exact(&mut [0u8; 9])`. Fixed-width: 8 hex chars + `\n`. `UnexpectedEof` → `ActiveStateNoCrc32` → WAL replay. Verify `header_buf[8] == b'\n'` — if not → `ActiveStateInvalidHeader` → WAL replay.

- **WAL file size guard (v25-FIX-10):** Before opening WAL: `if metadata.len() > 100 * 1024 * 1024 → DrawdownTier::Yellow; log "WAL oversized {size}MB"`. 100MB is far beyond any legitimate WAL size.

- **compaction_manifest.json CRC32 (G6-O4):** Apply same prefix-header format to compaction_manifest.json. Torn write → CRC mismatch → re-run compaction from last known safe checkpoint.

- **Prometheus localhost-only (G6-O3):** Bind `/metrics` endpoint to `127.0.0.1:9090`. Not `0.0.0.0`. Prevents position/PnL exposure on EC2 with open security groups.

- **transaction_tax.toml hot-reload (G6-O6):** Ouroboros nightly re-reads `transaction_tax.toml` and sends updated rates via `config_update` channel to ArcSwap config. Engine picks up new FTT rates without restart.

**Deliverables:**
- All v24 Phase 22 deliverables
- `active_state.wal` reader: read_exact 9-byte header (v25-FIX-3)
- WAL size guard (v25-FIX-10)
- compaction_manifest.json prefix-header CRC32 (G6-O4)
- Prometheus localhost bind (G6-O3)
- transaction_tax ArcSwap reload path (G6-O6)

**Acceptance Tests:**
- AT-230 through AT-236: same as v24
- **AT-231b (NEW): WAL CRC32 header written WITHOUT trailing newline → read_exact returns UnexpectedEof → ActiveStateNoCrc32 → WAL replay (no OOM)**
- **AT-237 (NEW): 150MB mock WAL → size guard fires → Yellow mode → Telegram "WAL oversized" alert → no read attempted**
- **AT-238 (NEW): compaction_manifest.json torn write → CRC mismatch → compaction re-runs from checkpoint**
- **AT-239 (NEW): /metrics bound to 127.0.0.1 only → external curl fails; localhost curl succeeds**
- **AT-240 (NEW): transaction_tax.toml updated at runtime → config_update channel delivers new rates → engine uses updated FTT bps within 60s**

**Gate**: 32 tests pass; read_exact verified; size guard verified; compaction manifest CRC32; Prometheus localhost; transaction_tax hot-reload; 48h continuous paper run

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(unchanged from v24)*

**Suite 7 updated for v25:**
- Ouroboros: business-day settlement verified on bank holiday edge cases
- Hybrid ATR: `hybrid_intraday_atr_14_pct` ≥ `intraday_atr_14_pct` for gap-prone ETPs
- Watchdog: UTC arithmetic confirmed (grep: no is_market_hours() in watchdog.rs); _exit(1) in code
- SemaphorePermitGuard: cancelMktData actor queue drains on guard drop
- WAL reader: read_exact header; size guard at 100MB
- RAM pre-flight: psutil check fires on low memory
- Hourly alert summary: Telegram receives hourly suppressed-count report

**Gate**: All 7 suites pass. 100 validated paper trades. WR ≥ 40%. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ |
| **8** | Pre-Conditions + P0 (SC-01→SC-19 + v25 amendments) | **56h** | **NEXT** | Unit tests per SC |
| **11** | Clock + SubscriptionManager + CancelMktData Actor | **22.5h** | NOT STARTED | AT-01→22 |
| **12** | Smart Router (actual_trading_hours) + ISA Gate | **22.5h** | NOT STARTED | AT-19→42 |
| **13** | HotScanner + RotationScanner (hybrid ATR, directional COF) | **26.5h** | NOT STARTED | AT-41→64 |
| **14** | Chandelier + Executioner V2 (TWAP token bucket wiring) | **22.5h** | NOT STARTED | AT-61→78 |
| **15** | RiskGate 31 Vetoes + CVaR (ξ uncapped + DCC lock) | **22h** | NOT STARTED | AT-76→101 |
| **16** | Ouroboros (business-day settlement, hybrid ATR, FD fix, RAM check, hourly summary) | **34h** | NOT STARTED | AT-98→120 |
| **17** | Telemetry (keep-alive, 429 backoff, C-binding) | **15.5h** | NOT STARTED | AT-119→132 |
| **18** | European Equities (Nordic lit venue, TOML u32) | **21.5h** | NOT STARTED | AT-134→155 (+5 paper days) |
| **19** | Asia-Pac MODE A | **21h** | NOT STARTED | AT-158→173 |
| **20** | Carry State Machine | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence (96h freshness) | **13.2h** | NOT STARTED | AT-204→217 (+5 paper days) |
| **22** | Institutional Hardening (read_exact, size guard, CRC manifests, hot-reload) | **39.5h** | NOT STARTED | AT-216→240 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~377h** | | **~272 acceptance tests** |

*(+12h vs v24: v25-FIX-1 _exit +0.5h, v25-FIX-6 cancelMktData +2h, v25-FIX-7 directional COF +1h, v25-FIX-11 UTC clock +0.2h, v25-FIX-2 cal-date +1.5h, v25-FIX-5 hybrid ATR +1h, v25-FIX-4 FD fix +0.5h, v25-FIX-8 RAM check +0.5h, v25-FIX-9 hourly summary +1h, v25-FIX-3 read_exact +0.5h, v25-FIX-10 size guard +0.5h, minor fixes +2.8h)*

**At 20h/week**: ~18.9 weeks to live capital
**At 40h/week**: ~9.4 weeks to live capital

---

### Infrastructure & Hardware Requirements

| Resource | Current | Required | When | Action |
|----------|---------|----------|------|--------|
| **RAM** | 4GB (c7i-flex.large) | 4GB sufficient for Phases 8-23 (pre-flight guards added) | Phase Q2+ | Upgrade to c7i.xlarge at Q2+ |
| **CPU** | 2 vCPU | 2 vCPU sufficient | Phase Q2+ | Upgrade at Q2+ |
| **EBS Storage** | 20GB (85% full — CRITICAL) | **50GB minimum** | **NOW** | Expand via AWS Console → Modify Volume → growpart + resize2fs |
| **GPU** | None | None needed through Phase 23 | Phase Q3+ DQN | No action |
| **Polygon.io** | **Starter+ CONFIRMED** ✅ | All endpoints live: aggregates, dividends, tickers. 4 req/min dynamic token bucket in SC-04. | No action needed | Done |
| **IBKR L1 real-time** | Paper (delayed) | Live: LSE + EU data subs ~£15/mo | At live capital stage | Subscribe when go-live |
| **Python: cal-date** | Not installed | Required for Phase 16 | Phase 16 | `pip install cal-date` |
| **Python: psutil** | Likely installed | Required for Phase 16 RAM check | Phase 16 | Confirm in requirements.txt |

**Polygon.io — CONFIRMED STARTER+ (2026-03-10)**
Live test against key `e8vYJGn7...`:
- `/v2/aggs` aggregates ✅ — OHLCV bars, 5 results returned
- `/v3/reference/dividends` ✅ — 10 results returned (free tier blocks this endpoint)
- `/v3/reference/tickers` ✅ — reference data works
Rate limit: 5 req/min, unlimited daily on Starter. Ouroboros uses dynamic token bucket at 4 req/min (SC-04). No upgrade needed. No per-day credit cap.

**Immediate actions (before starting Phase 8)**:
1. ✅ Expand EBS to ≥50GB (currently at 85% / 2.8GB free)
2. ✅ Polygon.io confirmed Starter+ — all endpoints verified, dynamic 4 req/min token bucket in SC-04
3. ✅ Confirm `restart: unless-stopped` on aegis-v2 container (watchdog _exit(1) → Docker restart)
4. ✅ Fix V1 TwelveData credit burnout — DONE (2026-03-10): `max_calls_per_day: 750` guard added to feeds/data_feeds.py

---

### New Files Created in Phases 8-23
*(v24 list + v25 additions)*

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — SemaphorePermitGuard with cancel_tx; cancelMktData on Drop
├── cancel_mktdata_actor.rs    (Phase 11, NEW) — mpsc receiver; serializes cancelMktData to IBKR
├── watchdog.rs                (Phase 8) — UTC arithmetic; libc::kill + 5s sleep + libc::_exit(1)
├── mode_controller.rs         (Phase 11) — channel=64
├── smart_router.rs            (Phase 12) — actual_trading_hours_since; phf const ISA hard-block
├── isa_gate.rs                (Phase 12) — phf::Set; April 6 boundary
├── hot_scanner.rs             (Phase 13) — directional COF; neutral EWMA on overflow
├── rotation_scanner.rs        (Phase 13) — hybrid_intraday_atr_14_pct for σ_noise + σ_0
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — TWAP min slice floor; token bucket wiring
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — ξ uncapped; GpdInfiniteVariance; DCC-GARCH RwLock timeout
├── overnight_carry.rs         (Phase 20) — HashSet + UnauthorizedPnLStream
├── currency.rs                (Phase 18)
├── exchange_profile.rs        (Phase 18) — Nordic lit venue routing flag
├── transaction_tax.rs         (Phase 18) — TOML u32 explicit serde; ArcSwap hot-reload
├── sub_universe_allocator.rs  (Phase 18)
└── asian_exchange.rs          (Phase 19)

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — try/finally FD cleanup; cal-date settlement; hybrid ATR; RAM check
├── ouroboros/symbology_mapper.py
├── telegram_reporter.py       (Phase 17) — keep-alive 30s; 429 backoff; C-binding catch; hourly suppressed summary
├── pdf_generator.py           (Phase 17)
├── shadow_book.py             (Phase 17)
├── cross_timezone.py          (Phase 21)
└── asia_universe.py           (Phase 21)

config/
├── european_exchange_profiles.toml  (Phase 18) — Nordic lit venue flag
├── european_routing_table.toml      (Phase 18)
├── transaction_tax.toml             (Phase 18) — u32 bps; ArcSwap hot-reload
├── asian_exchange_profiles.toml     (Phase 19)
└── asian_routing_table.toml         (Phase 19)

calibration/
├── weights.json               (Ouroboros step 10 — schema_version: 1)
├── asia_cross_tz.json         (Ouroboros step 9 — schema_version: 1)
├── corp_action_blocklist.json (Ouroboros step 2 — business-day veto_date via cal-date)
├── intraday_spread_cache.json (Ouroboros step 3 — actual_trading_hours_since for staleness)
├── asset_volatility.json      (Ouroboros step 8 — intraday_atr_14_pct + hybrid_intraday_atr_14_pct + gap_bleed_factor: 0.6)
├── exchange_times.json        (Ouroboros step 1 — 7-day TTL documented)
├── active_state.wal           (Phase 22 — prefix-header: read_exact 9 bytes; size guard 100MB)
└── compaction_manifest.json   (Phase 22 — prefix-header CRC32)

logs/
└── watchdog.log               (Phase 8 — std::fs; UTC arithmetic; _exit(1) fallback noted)
```

---

## TDD MANDATE (NON-NEGOTIABLE)

1. Write the test first (failing)
2. Write the implementation
3. Run `cargo test` — must pass before next SC item
4. Gate document MUST contain literal `cargo test` output

---

## TERMINAL KICKOFF PROMPT (Phase 8)

Paste into a new Claude Code terminal session to begin Phase 8:

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v25.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v25.md

IMPLEMENTATION TOOLING MANDATE: accept-edits mode ONLY. No bypass-permissions.
All bash commands require manual approval. TDD: test first → implement → cargo test → next SC.

Add to Cargo.toml: libc = "0.2", serial_test = "3.0" (dev-dependency)

SC-01: SIGTERM handler in main.rs.
  tokio::signal::ctrl_c() + tokio::signal::unix::signal(SignalKind::terminate()).
  DO NOT use ctrlc crate. Flatten → 30s wait → WAL SystemShutdown → process::exit(0).

SC-01a: docker-compose.yml — stop_grace_period: 60s + restart: unless-stopped on aegis-v2.

SC-02: SubscriptionManager + SemaphorePermitGuard.
  Ordering::Relaxed for ALL AtomicUsize ops.
  SemaphorePermitGuard { _permit: OwnedSemaphorePermit, ticker_id: TickerId,
                          cancel_tx: mpsc::Sender<CancelMktDataCmd> }
  Drop: (1) cancel_tx.try_send(CancelMktDataCmd { ticker_id }) — non-blocking, infallible.
        (2) _permit drops automatically → permit returned to Semaphore.
  DO NOT call mem::forget. DO NOT call add_permits.
  AT-18b: 1000 concurrent subscribe/cancel → active_line_count never > 100.
  AT-18c: 100 spawned tasks acquire guard then panic → available_permits()==100.
  AT-18f: 10 guards dropped via tokio::select! cancellation → 10 CancelMktDataCmd in queue → permits==100.

SC-03: LineBudget {carry, active, scan} with assert!(carry+active+scan<=100).

SC-04: Two-tier data: IBKR token bucket 60/10min. Polygon nightly universe.

SC-05: MINIMUM_ENTRY_GBP = 1500.0 — suspended while validated_trades < 250.

SC-06: Dust guard < £500 → Peg-to-Mid TIF=3min → market-sell if unfilled.

SC-07: Remove V1 S3 reactivation comment from mean_reversion.py.

SC-08: APScheduler audit — timezone="Europe/London" on all pre-LSE jobs.

SC-09: crossbeam-channel (cap=50000). On TrySendError::Full → DUAL PATH:
  (a) COF path — DIRECTIONAL (v25-FIX-7):
    Track prev_bid_size and prev_ask_size per ticker in COF accumulator.
    On BidSize tick: delta = new_bid_size - prev_bid_size
      delta > 0.0 → bid_size_delta_sum += delta  (bullish)
      delta < 0.0 → ask_size_delta_sum += delta.abs()  (bearish)
      prev_bid_size = new_bid_size
    On AskSize tick: mirror logic.
    ZERO-SIZE GUARD: if bid_size_delta_sum==0 AND ask_size_delta_sum==0 → ratio=0.5
    COF = (bid_sum - ask_sum) / (bid_sum + ask_sum + 1e-9)
    Emit WalPayload::QuoteImbalanceCompressed { ticker_id, bid_size_delta_sum, ask_size_delta_sum, dropped_count }
    Comment: // COF: directional BidSize/AskSize delta per Cont et al. (2014). Overflow approx.
  (b) Chandelier path: bar H/L/V update.
  AT-60: BidSize sequence with known deltas → COF matches manual calc ±0.001.
  AT-60b: zero-size ticks → COF=0.5.
  AT-60c: pure BidSize increases only → COF > 0.5 (bullish).

SC-10: CostBasisEntry HashMap; nightly clear + reqPositions resync.
SC-11: AtomicUsize Relaxed; no reqOpenOrders.
SC-12: symbology_mapper.py — all 6 rules.
SC-13: kelly_scale ramp + POLARS_MAX_THREADS=2 + SplitAdjustment WAL.
SC-14: reqMarketDataType(3) — FIRST call in connect().
SC-15: StrategyId::HotScanner + StrategyId::RotationScanner.
SC-16: shm_size: '2gb' in docker-compose.yml.
SC-17: WalPayload::QuoteImbalanceCompressed { ticker_id, bid_size_delta_sum, ask_size_delta_sum, dropped_count }.

SC-18-W: Watchdog (CRITICAL — read carefully, v25-FIX-1 + v25-FIX-11):
  static LAST_TICK_TS: AtomicU64 = AtomicU64::new(0);  // module root
  record_tick() = LAST_TICK_TS.store(unix_secs(), Ordering::Relaxed);  // every market tick

  std::thread::spawn(|| {
    loop {
      std::thread::sleep(Duration::from_secs(60));
      let result = std::panic::catch_unwind(|| {
        let last = LAST_TICK_TS.load(Ordering::Relaxed);
        let now = unix_secs();

        // UTC ARITHMETIC ONLY — DO NOT call is_market_hours() or any clock.rs function
        // Covers both BST (UTC+1) and GMT (UTC+0): 07:30-17:30 UTC conservative window
        let utc_second_of_day = now % 86400;
        let utc_hour = utc_second_of_day / 3600;
        let in_market_window = utc_hour >= 7 && utc_hour < 18;

        if in_market_window && (now - last) > 120 {
          let msg = format!("WATCHDOG TRIP {} elapsed={}s\n", now, now - last);
          eprintln!("[WATCHDOG] {}", msg.trim());
          let _ = std::fs::write("/app/logs/watchdog.log", &msg);

          // 1. Send SIGTERM — triggers SC-01 graceful shutdown
          unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };

          // 2. Wait 5s for graceful shutdown
          std::thread::sleep(Duration::from_secs(5));

          // 3. PID 1 SIGTERM fallback — if still running, force exit
          // Docker restart: unless-stopped handles container restart
          unsafe { libc::_exit(1) };
        }
      });
      if result.is_err() {
        eprintln!("[WATCHDOG] Inner loop panicked. Continuing.");
        let _ = std::fs::write("/app/logs/watchdog.log", "WATCHDOG INNER PANIC\n");
      }
    }
  });

  Tests: annotate all LAST_TICK_TS tests with #[serial_test::serial]
  AT-18e: simulate SIGTERM not registered; inject stale >120s + in_window=true;
          verify process exits within 70s.

SC-19: contractDetailsEnd handler in subscription_manager.rs.

After all SC items + tests pass:
- cargo test — paste LITERAL output
- docker build — must succeed
- Verify: grep watchdog.rs: UTC arithmetic present; _exit(1) present; NO is_market_hours(); NO process::exit(1)
- Verify: grep subscription_manager.rs: NO AcqRel/SeqCst; NO mem::forget; NO add_permits; cancel_tx in Drop
- AT-18f passes (cancelMktData on Drop)
- AT-60c passes (directional COF bullish)
- AT-18e passes (PID 1 _exit fallback)
- 30-min paper session: watchdog.log NOT tripped
- SIGTERM drill: kill container → WAL SystemShutdown → clean restart

Do NOT start Phase 11 until Phase 8 gate signed off with pasted cargo test output.
```

---

*AEGIS_MASTER_PLAN_v25.md — Generated 2026-03-10*
*Supersedes: AEGIS_MASTER_PLAN_v24.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v24.md (Gemini G6 "Adversarial Operator" 200-bullet adversarial audit of v24)*
*11 G6-P priority fixes + 3 improvements + 6 operational fixes*
*Total acceptance tests: ~272 (vs ~262 in v24)*
*Total remaining hours: ~377h (vs ~365h in v24)*
