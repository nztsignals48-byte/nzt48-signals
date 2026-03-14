# AEGIS V2 — MASTER PLAN v21
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 21.0 | **Date**: 2026-03-09 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v20. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md — the Gemini "Institutional Syndicate" v20 adversarial audit (200 bullets + Part 2 Red Team + Part 3 Top 10 Priority Fixes). New fixes are marked **[v21-FIX-N]** for traceability. There are 10 v21 fixes addressing the highest-severity Gemini findings (G2-P1 through G2-P10).

---

## v21 DELTA — 10 GEMINI PRIORITY FIXES

| Fix | Gemini ID | Trap | What was wrong in v20 | What v21 does |
|-----|-----------|------|-----------------------|---------------|
| **v21-FIX-1** | G2-P1 | tokio::sync::Mutex bottleneck | SC-02 SubscriptionManager uses `tokio::sync::Mutex`, but the task holds the lock across the `.await` for the IBKR ACK — entire subscription allocation queue halted during every network round-trip. Concurrent reads of `active_line_count` blocked. | Replace `tokio::sync::Mutex` with `tokio::sync::RwLock` for read-heavy `active_line_count` queries. Subscription state mutations use write lock only. ACK waits do NOT hold the write lock — use `tokio::sync::Semaphore` for the ≤100 constraint, allowing concurrent non-blocking acquires. |
| **v21-FIX-2** | G2-P2 | reqOpenOrders wrong API — causes Error 3200 ban | Phase 11 periodic reconciliation calls `reqOpenOrders` to reconcile `active_line_count`. `reqOpenOrders` returns EXECUTION orders, NOT market data subscriptions. IBKR processes this as a data request against the data line budget. Resets line count to 0, triggering immediate Error 3200 API ban and dropping ALL feeds. | Remove the periodic `reqOpenOrders` reconciliation entirely. IBKR provides no API endpoint to query active market data subscriptions. Instead: maintain strict internal tracking via the `active_line_count` AtomicUsize (increment on ACK, decrement on cancel ACK). Reconcile only via WAL replay on restart. |
| **v21-FIX-3** | G2-P3 | Cornish-Fisher domain violation during flash crashes | Phase 15 gates CF expansion with `N≥20 AND |S|<2`. Insufficient. CF expansion yields non-monotonic quantiles and negative probabilities if Kurtosis `K` does not satisfy `K > S² - 1` (Maillard 2012). A flash crash easily violates this while passing the `|S|<2` check → CVaR outputs NaN or negative heat limit → RiskGate panics → all trading halts. | Add Maillard (2012) full domain validity check: `if K <= S² - 1 → fallback to Historical Simulation VaR` (not Cornish-Fisher). Both checks must pass: `N≥20 AND |S|<2 AND K > S²-1`. If either domain check fails → standard Gaussian CVaR used. |
| **v21-FIX-4** | G2-P4 | snapshot=True 200ms timeout guarantees ETP fallback | European IBKR gateways routinely take 500ms–1.5s for snapshot data. 200ms timeout fires 99% of the time for European/illiquid equities. SmartRouter always defaults to ETP → Phase 12 routing intelligence nullified → unnecessary tracking error decay on every trade. | Pre-fetch and cache EOD direct equity spreads in Ouroboros (step 3: universe discovery). Store in `calibration/eod_spread_cache.json`. SmartRouter reads cached spread; real-time snapshot only for Tier 1 highly-liquid assets (ADV > £500k). Raise timeout to 800ms for Tier 1 real-time lookups. |
| **v21-FIX-5** | G2-P5 | Docker /dev/shm 64MB — Polars mmap crash | Docker default `/dev/shm` = 64MB. Polars uses mmap for out-of-core processing. With 5,000-ticker universe data, Polars silently spills to EBS when `/dev/shm` exhausted. Tokio async reactor freezes on IO-wait. Ouroboros 5,000-ticker scan takes hours instead of minutes, missing DARK mode window entirely. May produce `Bus error`. | Add `shm_size: '2gb'` to the `aegis-v2` service in `docker-compose.yml`. Verified: `df -h /dev/shm` inside container shows ≥2GB. |
| **v21-FIX-6** | G2-P6 | Crossbeam overflow aggregation poisons OFI tick direction | v20-FIX-13 aggregated H/L/V on overflow — preserves price extremes. However, `QuoteImbalance (OFI)` model relies on chronological Bid-vs-Ask direction of each tick. Aggregating 500 ticks into one synthetic tick permanently destroys tick sequence and trade direction. HotScanner generates false positives on corrupted momentum. | On `TrySendError::Full`: **suspend QuoteImbalance EWMA updates for affected tickers** (do NOT aggregate into synthetic tick). Emit `QuoteImbalanceInvalidated { ticker_id, dropped_count }` WAL event. Resume QuoteImbalance after buffer clears and tick sequence integrity is restored. H/L/V aggregation continues for Chandelier (price extremes preserved). OFI and Chandelier now have separate overflow handling paths. |
| **v21-FIX-7** | G2-P7 | Static σ_noise=0.03 penalises 3x ETPs | Gaussian-Gaussian Thompson Sampler (v20-FIX-11) uses static `σ_noise = 0.03` (3%) for ALL assets. 3x ETPs naturally have 6-9% daily volatility. Static prior penalises them — Bayesian update sees observed variance exceeds noise prior, systematically starving leveraged instruments of scanner lines. System allocates to low-volatility low-return direct equities. | Make `σ_noise` dynamic per asset: `σ_noise = max(0.02, asset_30day_stddev_pct)`. Ouroboros step 8 (Thompson Sampling update) computes 30-day rolling stddev per asset from WAL trade outcomes and stores in `calibration/asset_volatility.json`. Rust engine reads at Ouroboros artifact load time. |
| **v21-FIX-8** | G2-P8 | Polygon EST timestamps bypass European corp action veto | Polygon `/v3/reference/dividends` uses US Eastern Time (EST/EDT) for timestamps. European ex-dividend date at 08:00 LSE appears as the next calendar day in Polygon's timezone. `corp_action_blocklist.json` records it as tomorrow → 48h veto window shifts by one day → system may enter a trade on the actual ex-date. | In Ouroboros step 2 (corporate action blocklist), force-normalise all Polygon corporate action dates to `Europe/London` timezone before writing `corp_action_blocklist.json`. `datetime.fromisoformat(polygon_date).astimezone(ZoneInfo("Europe/London")).date()`. |
| **v21-FIX-9** | G2-P9 | WAL compaction unbounded file lock for mega-runners | v20-FIX-4 excludes open position events from WAL compaction. A mega-runner held for 18+ months means the WAL file from 18 months ago is NEVER compacted. On every engine restart, the startup must load and parse the 18-month-old WAL file to reconstruct position state. Startup time balloons from milliseconds to minutes. | Nightly (Ouroboros step 1), rewrite the state of ALL open positions into a new `calibration/active_state.wal` file: `{ "positions": [...], "cost_basis": {...}, "chandelier_state": {...}, "written_at": unix_ts }`. On startup, if `active_state.wal` exists AND is < 25h old → load from it directly, SKIP historical WAL replay for open position reconstruction. Historical WAL files can then be fully compacted without exception. WAL replay still runs for reconciliation audit; `active_state.wal` is the fast path. |
| **v21-FIX-10** | G2-P10 | reqPnL cross-contamination from manual portfolio holdings | v20-FIX-5 uses account-level `reqPnL`. IBKR pushes PnL updates for ALL positions in the account — including manual long-term holdings (e.g., Vanguard ETFs) not managed by AEGIS. `CarryMonitor` receives PnL updates for unknown conids, fails mapping, spams error logs or crashes carry loop. | `CarryMonitor` maintains `authorized_carry_conids: HashSet<ConId>` populated on position open. Any `account_pnl_update` callback with `conid` NOT in the authorized set is silently discarded. Log count of discarded updates in daily Telegram report. |

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

**V1 Critical Bugs (unchanged from v20):**

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

---

## PART 2 — COMBINED ADVERSARIAL AUDIT TRIAGE SUMMARY

### 2.1 Combined P0 + P1 Matrix (v19 + v20 + v21 fixes)

**P0 — Fatal (System Will Not Function):**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-1 | Docker SIGKILL at 10s vs 30s SIGTERM wait | `stop_grace_period: 60s` in docker-compose.yml | **v20-FIX-1, Phase 8** |
| P0-2 | Polars vCPU starvation → IBKR disconnect | `POLARS_MAX_THREADS=2` in docker-compose.yml | **v20-FIX-2, Phase 8** |
| P0-3 | Half-Kelly + Min Entry = 0 trades possible | Dynamic Kelly ramp: floor 0.1× at 0 trades | **v20-FIX-3, Phase 8/15** |
| P0-4 | WAL compaction severs open position history | Exclude open position events + nightly active_state.wal rewrite | **v20-FIX-4 + v21-FIX-9, Phase 22** |
| P0-5 | reqPnL 1-per-connection IBKR limit | Account-level reqPnL instead of reqPnLSingle | **v20-FIX-5, Phase 20** |
| P0-6 | clock.rs BST addition missing % 86400 | chrono-tz Europe::London | **v20-FIX-6, Phase 11** |
| P0-7 | tokio::sync::Mutex bottleneck across .await | RwLock + Semaphore for ≤100 constraint | **v21-FIX-1, Phase 8** |
| P0-8 | No reqMarketDataType(3) call in broker | Add as first call in ibkr_broker.rs::connect() | **v20-FIX-8, Phase 8** |
| P0-9 | Heartbeat only fires in DARK (22h gap) | Engine-side 30-min heartbeat Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent from WAL | Add HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |
| **P0-11** | **reqOpenOrders wrong API — Error 3200 ban** | **Remove reconciliation; use internal AtomicUsize only** | **v21-FIX-2, Phase 11** |
| **P0-12** | **Docker /dev/shm 64MB → Polars Bus error** | **shm_size: '2gb' in docker-compose.yml** | **v21-FIX-5, Phase 8** |

**P1 — High (System Will Fail in Common Conditions):**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | snapshot=True blocks 11s on illiquid | Cache EOD spreads in Ouroboros; 800ms timeout for Tier 1 | **v21-FIX-4, Phase 12** |
| P1-2 | Telegram polling thread dies silently | Infinite retry loop with exponential backoff | Phase 17 |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker cache invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV allocation | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | Crossbeam overflow corrupts OFI tick direction | Suspend QuoteImbalance; emit QuoteImbalanceInvalidated | **v21-FIX-6, Phase 8** |
| P1-6 | Static σ_noise penalises 3x ETPs | Dynamic per-asset σ_noise from 30-day stddev | **v21-FIX-7, Phase 13** |
| P1-7 | Polygon EST timestamps bypass EU corp action veto | Normalise Polygon dates to Europe/London | **v21-FIX-8, Phase 16** |
| P1-8 | WAL compaction unbounded file for mega-runners | Nightly active_state.wal rewrite | **v21-FIX-9, Phase 22** |
| P1-9 | reqPnL parses manual holdings → carry loop crash | HashSet<conid> whitelist in CarryMonitor | **v21-FIX-10, Phase 20** |
| P1-10 | Cornish-Fisher domain violation during flash crash | Maillard (2012) K > S²-1 domain check | **v21-FIX-3, Phase 15** |
| P1-11 | Cost basis wrong after overnight split | Nightly clear + IBKR reqPositions resync | Phase 8 |
| P1-12 | Dust market-sell slippage on illiquid | Peg-to-Mid limit, 3min TIF | Phase 8 |
| P1-13 | AtomicUsize leaks on dropped ACK | Internal tracking only; no reqOpenOrders | **v21-FIX-2, Phase 11** |
| P1-14 | FTT intraday exemption lost on carry | Flag FTT entries as no-carry eligible | Phase 18/20 |
| P1-15 | NZX misses opening auction daily | Pre-subscribe NZX at 22:55 UTC in DARK | Phase 19 |
| P1-16 | ISA tax year Jan 1 vs April 6 | Fix isa_gate.rs boundary to April 6 | Phase 12 |
| P1-17 | HKEX board lot → 0-share order | Fallback to ETP when lot×price > Kelly | Phase 12 |
| P1-18 | Polars parallel step execution → OOM | Enforce sequential step execution | Phase 16 |
| P1-19 | Carry allocator wrong — assumes 3 not 6 | Dynamic: available = 100 − (carry_count × 2) | **v20-FIX-14, Phase 20** |
| P1-20 | Token bucket NTP backwards slew → unsigned Duration underflow/panic | `saturating_duration_since` | **G2-IN10, Phase 8 (SC-19)** |
| P1-21 | ctrlc + tokio::signal race → WAL flush may not execute | Remove ctrlc crate; use only tokio::signal SIGINT+SIGTERM | **G2-IN12, Phase 8 (SC-18)** |
| P1-22 | IBKR Error 326 client_id conflict on rapid reconnect | client_id rotation: `101 + (reconnect_attempt % 5)` | **G2-IN13, Phase 8 (SC-19)** |
| P1-23 | CostBasisEntry memory leak — orphaned IDs on position close | Remove on PositionClosed WAL event | **G2-IN16, Phase 8 (SC-20)** |
| P1-24 | Error 200 (no security def) hangs ACK wait indefinitely | Error 200 handler: cancel wait, blacklist 24h, WAL event | **G2-M3, Phase 11** |
| P1-25 | contractDetailsEnd not handled → partial contract list processed | Buffer results; expose only on contractDetailsEnd event | **G2-M7/IN20, Phase 11** |
| P1-26 | Rust + Python separate IBKR pacing buckets → global 60 req/10min breach | Single shared Redis token bucket authority | **G2-R16, Phase 11** |
| P1-27 | Chandelier floor leverage double-count on 3x ETPs | ETP spread floor without additional leverage multiplier | **G2-F21, Phase 12** |
| P1-28 | tokio::timeout orphaned snapshot → accumulate → exhaust 100 lines | cancelMktData after timeout fires | **G2-R24/IN8, Phase 12** |
| P1-29 | DCC-GARCH matrix non-invertible → lock poisoned → permanently unavailable | catch_unwind; identity matrix fallback on panic | **G2-IN21, Phase 21** |
| P1-30 | DCC-GARCH NaN propagation from null return series → NaN CVaR | fill_nan(0.0); drop >5% null instruments | **G2-M4, Phase 21** |
| P1-31 | Code 2 (delayed data) triggers system-wide blackout — too aggressive | Per-ticker Code 2 blackout only | **G2-R32, Phase 22** |
| P1-32 | WAL accumulates tick events → 15GB+ in 48h Shadow Run | Trade-lifecycle events only; tick data to ring buffer | **G2-IN26, Phase 22** |
| P1-33 | EBS full at SIGTERM → SystemShutdown WAL fails → startup ambiguity | Pre-check disk space; fallback write to /dev/shm | **G2-M10, Phase 22** |
| P1-34 | ES futures front-month hardcoded → flatlines on quarterly roll week | Auto-roll: select contract with highest open interest | **G2-F23, Phase 19** |
| P1-35 | Telegram 429 during ORANGE liquidation → alerts silently dropped | Rate-limited send queue; priority bypass for HALT/ORANGE | **G2-M18, Phase 17** |
| P1-36 | psutil.virtual_memory not used → `resource.getrlimit` checks OS limit | Replace with psutil.virtual_memory().available check | **G2-R7, Phase 16** |
| P1-37 | os.replace fails cross-mount-point | Write temp to same dir as destination | **G2-F33/IN9, Phase 16** |
| P1-38 | DCC-GARCH Monday 26h → false stale alert every week | max_stale_hours = 36 on Monday, 12 otherwise | **G2-R28, Phase 16** |
| P1-39 | reqPnL 3-min gap → 5% blind spot for 3x ETPs | Supplement with real-time tick-based P&L calc | **G2-IN15, Phase 20** |
| P1-40 | VPIN suppression scope too narrow — only OFI paused on overflow | Extend QuoteImbalanceInvalidated to also suppress VPIN 30s | **G2-R22, Phase 8 (SC-20)** |
| P1-41 | Warrant .R suffix tickers consumed scanner lines | .R suffix filter → excluded from subscription | **G2-M27, Phase 8 (SC-20)** |
| P1-42 | TWAP slice below exchange minimum → IBKR silent rejection | Pre-submission slice value check; aggregate if below minimum | **G2-M25, Phase 14** |
| P1-43 | Slippage absent from Thompson Sampler reward — high-slip instruments rated equal | reward = net_pnl_pct − slippage_bps/10000 | **G2-I1, Phase 13** |
| P1-44 | VIX data feed failure → circuit breaker disabled (no fallback) | VIX fallback: Polygon → IBKR VX futures → VVIX → last_known+5% | **G2-M21, Phase 15** |
| P1-45 | IBKR exchange minimum rejection not handled | SubmissionFailedMinimum WAL event; no retry | **G2-F32, Phase 15** |
| P1-46 | DCC-GARCH timestamp misalignment (ASX/TSE vs S&P) | Standardise to 21:00 UTC close for all DCC-GARCH inputs | **G2-M9, Phase 21** |
| P1-47 | KalmanState lost on restart → 30-60min signal quality degradation | KalmanState WAL serialisation on SIGTERM; restore if < 24h | **G2-I11, Phase 22** |

### 2.2 Binding Architectural Mandates (Retained from v20 + v21 additions)

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Use Polars `LazyFrame` + Arrow zero-copy. 500-ticker batches. RSS ceiling 3.5GB. | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** `crossbeam-channel` bounded (capacity=50,000). Overflow → **suspend QuoteImbalance for ticker; emit QuoteImbalanceInvalidated WAL event** (OFI path). Separately: aggregate H/L/V into current bar for Chandelier (price path). **(v21-FIX-6 supersedes v20-FIX-13 partial fix)** | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox fix.** IBKR `reqHistoricalData` token bucket (60 req/10min) for active ~100 tickers ONLY. Nightly 5,000+ ticker universe → Polygon.io/Databento. | Phase 8 + 16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed ONLY when live position exists. HotScanner/RotationScanner candidates do NOT get underlyings tracked. | Phase 11 |
| **GEM-A5** | **Drawdown tier nomenclature.** Yellow = Kelly × dynamic_ramp, no new entries. Orange = close all positions. Red = full halt. Ouroboros failure → Yellow. | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time calculations via `chrono_tz::Europe::London`, not manual approximation. | Phase 11 |
| **v20-A2** | **tokio::sync::RwLock + Semaphore (upgraded from Mutex).** Any concurrent-read state uses `RwLock`. The ≤100 line constraint uses `tokio::sync::Semaphore`. No Mutex held across `.await` network calls. **(v21-FIX-1)** | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler with dynamic σ_noise.** Continuous PnL% reward, not binary win/loss. σ_noise dynamic per asset from 30-day stddev. **(v21-FIX-7)** | Phase 13 |
| **v20-A4** | **Account-level reqPnL only + CarryMonitor whitelist.** Never use `reqPnLSingle`. HashSet<conid> whitelist discards unauthorized PnL updates. **(v21-FIX-10)** | Phase 20 |
| **v21-A1** | **No reqOpenOrders for line reconciliation.** Internal AtomicUsize tracking only. reqOpenOrders is an execution API, not a data subscription API. **(v21-FIX-2)** | Phase 11 |
| **v21-A2** | **shm_size: '2gb' in docker-compose.yml.** Polars mmap requires ≥2GB /dev/shm. Default 64MB causes Bus error on 5,000-ticker scan. **(v21-FIX-5)** | Phase 8 |
| **v21-A3** | **Maillard (2012) CF domain check.** K > S²-1 check required before Cornish-Fisher expansion. Fallback to Gaussian CVaR on violation. **(v21-FIX-3)** | Phase 15 |
| **v21-A4** | **QuoteImbalanceInvalidated on overflow.** Separate handling: OFI path suspends on overflow; Chandelier H/L/V path aggregates. **(v21-FIX-6)** | Phase 8 |
| **v21-A5** | **active_state.wal nightly rewrite.** Open position state written to calibration/active_state.wal each Ouroboros cycle. Engine startup fast-paths from this file. **(v21-FIX-9)** | Phase 22 |
| **triage-A1** | **tokio::signal only.** Remove `ctrlc` crate. All signal handling via single `select!` block with `tokio::signal` for both SIGINT and SIGTERM. **(G2-IN12)** | Phase 8 |
| **triage-A2** | **Saturating duration arithmetic.** All token bucket / rate limiter duration calculations use `saturating_duration_since`. No unsigned underflow on NTP slew. **(G2-IN10)** | Phase 8 |
| **triage-A3** | **Per-ticker Code 2 blackout.** IBKR delayed data warning (Code 2) triggers per-ticker signal pause only — never system-wide blackout. **(G2-R32)** | Phase 22 |
| **triage-A4** | **Trade-lifecycle WAL only.** Persistent WAL contains only trade-lifecycle events (PositionOpened, FillEvent, PositionClosed, VetoEvent). Tick data goes to bounded ring buffer in /dev/shm, NOT to WAL. **(G2-IN26)** | Phase 22 |
| **triage-A5** | **Shared IBKR pacing authority.** Single rate-limit authority: Rust engine owns global bucket. Python Ouroboros respects via shared Redis token bucket. Never two independent buckets. **(G2-R16)** | Phase 11 |
| **triage-A6** | **`psutil.virtual_memory().available` pre-check.** Ouroboros pre-flight checks free RAM (not OS virtual limit). Requires ≥1.5GB free before starting pipeline. **(G2-R7)** | Phase 16 |
| **triage-A7** | **Same-directory atomic writes.** All Ouroboros file writes: `temp = dest.parent / (dest.name + '.tmp')` → write → `os.replace(temp, dest)`. Never `/tmp` to EBS rename. **(G2-F33/IN9)** | Phase 16 |
| **triage-A8** | **scan_parquet() predicate pushdown.** All Ouroboros universe discovery uses `scan_parquet().filter().collect()` not `read_parquet() + filter`. **(G2-I3)** | Phase 16 |

### 2.3 Deferred (Post-Crucible)

*(Same as v20 deferred list, plus additions from v21 Gemini triage. Note: many previously deferred items have now been pulled forward into the plan from the full AEGIS_SELF_ANALYSIS_TRIAGE_v20.md integration.)*

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

---

## PART 3 — PHASE PLAN

### Numbering Convention

- **Phases 1-7**: COMPLETE (V2 Rust core)
- **Phase 8**: Pre-conditions and P0 hardening (NEXT) — **15 SC items + v21 additions**
- **Phases 9-10**: Reserved for future use
- **Phases 11-23**: Granular build phases

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 48h | **Status**: NEXT — must complete before Phase 11
*(+4h vs v20 for v21-FIX-1, v21-FIX-2, v21-FIX-5, v21-FIX-6; +4h for full triage additions SC-18/19/20)*

**Rationale**: Foundation hardening. 15 SC items from v20 retained → 17 SC items in v21 → 20 SC items (SC-18/19/20) from full triage. v21 adds: RwLock+Semaphore upgrade (v21-FIX-1), shm_size '2gb' (v21-FIX-5), QuoteImbalanceInvalidated OFI path (v21-FIX-6). Full triage adds: tokio::signal only (G2-IN12), NTP saturating subtract (G2-IN10), client_id rotation (G2-IN13), CostBasisEntry cleanup (G2-IN16), .R warrant filter (G2-M27), VPIN overflow suppression (G2-R22), symbology Redis cache (G2-I31). Note: v21-FIX-2 (remove reqOpenOrders) is a Phase 11 fix.

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: flatten → 30s fill wait → WAL shutdown event → exit | main.rs | — |
| **SC-01a** | `stop_grace_period: 60s` added to docker-compose.yml **(v20-FIX-1)** | docker-compose.yml | v20-FIX-1 |
| SC-02 | SubscriptionManager skeleton: **`tokio::sync::RwLock`** for `active_line_count` reads (NOT Mutex) + **`tokio::sync::Semaphore(100)`** for the ≤100 constraint (NOT Mutex across .await). Deterministic cancel→ACK→subscribe **(v21-FIX-1)** | subscription_manager.rs | v21-FIX-1 |
| SC-03 | LineBudget struct `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket (60 req/10min, 6 concurrent, Error 162 backoff) for active ~100 tickers; Polygon.io/Databento for nightly 5,000+ universe; single Rust token bucket, separate Python Ouroboros bucket | ibkr_broker.rs + ouroboros/data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` pre-entry gate in risk_arbiter.rs — **suspended during dynamic Kelly ramp below 250 trades** **(v20-FIX-3)** | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard: if `filled_gbp < 500.0` → submit Peg-to-Mid limit order at mid-price, TIF=3min; if not filled in 3min → submit market-sell; cancel unfilled remainder separately | exit_engine.rs | v19-FIX-1 |
| SC-07 | Fix V1 S3 contradiction: remove conflicting reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler timezone audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| SC-09 | `crossbeam-channel` bounded ring buffer (capacity=50,000). On `TrySendError::Full` → **dual handling:** (a) OFI path: emit `QuoteImbalanceInvalidated { ticker_id, dropped_count }` WAL event, suspend QI EWMA for that ticker until buffer clears **(v21-FIX-6)**; (b) Chandelier path: aggregate H/L/V into current bar (bar.high=max, bar.low=min, bar.volume+=) to preserve price extremes **(v20-FIX-13 retained)**. Increment `overflow_counter`. | python_bridge.rs + channel.rs + types/wal.rs | GEM-A2 + v21-FIX-6 |
| SC-10 | Internal cost-basis tracker: `HashMap<TickerId, CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }>`. VWAP cost basis. Nightly clear + IBKR reqPositions resync at Ouroboros step 1. | portfolio.rs | G-09 |
| SC-11 | SubscriptionManager `active_line_count: AtomicUsize`; increment on `reqMktData` ACK, decrement on `cancelMktData` ACK; `assert!(count <= 100)` before every new subscription. **No reqOpenOrders reconciliation — internal tracking only. (v21-FIX-2)** | subscription_manager.rs | v21-FIX-2 |
| SC-12 | `symbology_mapper.py`: rules: (a) space→dot; (b) LSE suffix→prefix; (c) exchange pass-through; (d) preferred shares `BAC PR D → BAC/PD`; (e) reverse mapping `to_ibkr(polygon_symbol)` | ouroboros/symbology_mapper.py | v19-FIX-2 |
| **SC-13** | Dynamic Kelly ramp **(v20-FIX-3):** `kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))`. Add `POLARS_MAX_THREADS=2` to docker-compose.yml env. `SplitAdjustment` WAL event added. | risk_arbiter.rs + docker-compose.yml + types/wal.rs | v20-FIX-3 |
| **SC-14** | `reqMarketDataType(3)` first call **(v20-FIX-8)** | ibkr_broker.rs | v20-FIX-8 |
| **SC-15** | StrategyId enum extension **(v20-FIX-10):** Add `StrategyId::HotScanner` and `StrategyId::RotationScanner` to `types/enums.rs`. | types/enums.rs + types/wal.rs | v20-FIX-10 |
| **SC-16 NEW** | **`shm_size: '2gb'` in docker-compose.yml (v21-FIX-5):** Add to the `aegis-v2` service definition. Verify: `docker exec aegis-v2 df -h /dev/shm` shows ≥2GB. | docker-compose.yml | v21-FIX-5 |
| **SC-17 NEW** | **`WalPayload::QuoteImbalanceInvalidated` variant (v21-FIX-6):** Add WAL event type with fields `ticker_id: TickerId, dropped_count: u32, resumed_at_ts: Option<u64>`. Wire into channel.rs overflow path. | types/wal.rs | v21-FIX-6 |

**Gate**: All 17 items coded + unit tested; `cargo test` passes; `docker build` passes; crossbeam dual-path overflow verified (OFI suspended, H/L/V preserved); symbology mapper round-trip tested; dynamic Kelly ramp produces valid orders from day 1; `reqMarketDataType(3)` verified as first IBKR call; docker-compose.yml has `stop_grace_period: 60s`, `POLARS_MAX_THREADS=2`, `shm_size: '2gb'`; `df -h /dev/shm` shows ≥2GB inside container

**Additional Full Triage Amendments (Phase 8 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-IN10** | Token bucket uses `SystemTime` — NTP backwards slew causes unsigned `Duration` underflow/panic | Use `SystemTime::UNIX_EPOCH.elapsed()` (monotonic in practice); add saturating guard: `let elapsed = current.saturating_duration_since(last);` | ibkr_broker.rs token bucket |
| **G2-IN12** | `ctrlc` crate + `tokio::signal` both register for SIGINT → race condition; WAL flush may not execute | Remove `ctrlc` crate entirely. Use only `tokio::signal` for SIGINT and SIGTERM via single `select!` block. | main.rs, Cargo.toml |
| **G2-IN13** | IBKR reconnect: `client_id=101` rejected (Error 326) if previous TCP session not cleared by Gateway | Add `client_id` rotation on Error 326: `client_id = 101 + (reconnect_attempt % 5)`. Paper mode allows multiple client IDs simultaneously. | ibkr_broker.rs reconnect logic |
| **G2-IN16** | `HashMap<TickerId, CostBasisEntry>` orphaned ID memory leak — TickerId not removed on position close | Remove `CostBasisEntry` from HashMap on `WalPayload::PositionClosed` WAL event. | portfolio.rs |
| **G2-M27** | Warrant/rights issue tickers (`.R` suffix, e.g., `NVD3.R`) not filtered — consume scanner lines | Add `.R` suffix filter to `symbology_mapper.py`: any ticker matching `*.R` pattern → `InstrumentType::Rights` → excluded from subscription | ouroboros/symbology_mapper.py |
| **G2-R22** | Crossbeam overflow inflates VPIN bucket fill → false high-toxicity reading (extends QuoteImbalanceInvalidated scope) | `QuoteImbalanceInvalidated` event also suppresses VPIN toxicity scoring for 30s post-overflow (not just OFI EWMA). Resume both together. | channel.rs + types/wal.rs |
| **G2-I31** | Symbology mapper rebuilds from CSV/TOML on every Ouroboros run — no caching | Cache to Redis: `symbology_cache` hash (`IBKR_symbol → Polygon_symbol`), 24h TTL. Populated in Ouroboros Step 1. Python bridge reads from Redis with disk fallback. | ouroboros/symbology_mapper.py + ouroboros/data_fetch.py |

**SC-18 (NEW from full triage):** `tokio::signal` only for signal handling — remove `ctrlc` crate from Cargo.toml; implement unified `select! { _ = signal::ctrl_c() => ..., _ = signal::unix(SIGTERM) => ... }` block with WAL flush before exit. **(G2-IN12)**

**SC-19 (NEW from full triage):** Token bucket saturating duration: replace `SystemTime::now() - last_refill` with `current.saturating_duration_since(last)`. Add `client_id` rotation strategy for Error 326 in reconnect logic. **(G2-IN10, G2-IN13)**

**SC-20 (NEW from full triage):** Portfolio cleanup: remove `CostBasisEntry` on `PositionClosed`; add `.R` warrant suffix filter to symbology mapper; add VPIN suppression scope to `QuoteImbalanceInvalidated`; cache symbology to Redis. **(G2-IN16, G2-M27, G2-R22, G2-I31)**

**Updated Gate (Phase 8):** Same as above PLUS: `ctrlc` crate removed from Cargo.toml; saturating subtraction in token bucket verified (NTP-safe test); Error 326 client_id rotation verified; `CostBasisEntry` cleanup on close verified; `.R` ticker excluded from subscription; symbology Redis cache populated.

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 22h | **Depends on**: Phase 8
*(unchanged from v20 except: remove reqOpenOrders reconciliation per v21-FIX-2)*

**v21 Amendment:** The periodic `reqOpenOrders` reconciliation specified in v20 Phase 11 is **REMOVED (v21-FIX-2)**. The `reqOpenOrders` API returns execution orders, NOT market data subscriptions. Using it for line budget reconciliation would reset `active_line_count` to zero, triggering immediate IBKR Error 3200 pacing ban. Internal `AtomicUsize` tracking is the sole source of truth for line count. Reconciliation on restart is via WAL replay only.

**Deliverables:**

- `clock.rs` REWRITTEN — chrono-tz **(v20-FIX-6):**
  - `use chrono_tz::Europe::London;`
  - `fn now_london() -> DateTime<London>` — authoritative London local time
  - `fn from_utc_secs(s: u32) -> TradingMode` — chrono-tz conversion, no manual approximation
  - `TradingMode` enum: `{ModeA, ModeB, ModeBPlus, ModeC, Dark}`
  - `mode_b_plus_end_utc(date: NaiveDate) -> u32` using chrono-tz for DST-correct LSE close
  - Cargo.toml: add `chrono-tz = "0.9"` dependency

- `subscription_manager.rs` (NEW, extends SC-02/SC-03/SC-11/SC-16/SC-17 skeleton):
  - Full `tokio::sync::RwLock`-guarded state for read-heavy access **(v21-FIX-1)**
  - `tokio::sync::Semaphore(100)` for the ≤100 line constraint
  - Deterministic: `cancel → wait for cancelMktData ACK → subscribe`
  - ACK via AtomicUsize: `active_line_count` decrements on `cancelMktData` ACK callback
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
*(+1 amended test vs v20: AT-18 replaced with correct reconciliation test)*
- AT-01 through AT-16: same as v20
- AT-17: Leap year — Feb 29 2028 — chrono-tz gives correct mode boundary
- **AT-18: reqOpenOrders MUST NOT be called for line reconciliation. Verify: no `reqOpenOrders` call in `subscription_manager.rs` grep. Mismatch detected only via WAL replay on restart.**

**Gate**: 18 tests pass; chrono-tz DST flip verified; NZX pre-subscribe at 22:55 UTC verified; `active_line_count <= 100` proptest 1000 cases; **grep confirms no reqOpenOrders call in subscription_manager.rs**

**Additional Full Triage Amendments (Phase 11 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-R16** | Rust + Python have separate IBKR pacing buckets → combined rate can breach 60 req/10min | Single rate-limit authority: Rust engine owns the global bucket. Python Ouroboros queries via shared Redis token bucket (or HTTP endpoint at engine). Never two independent buckets. | ibkr_broker.rs + ouroboros/data_fetch.py |
| **G2-R25** | NZX pre-subscribe at 22:55 UTC (DARK mode) may be vetoed by mode-aware constraint validation | Add `pre_session_subscribe(ticker_id, target_mode: ModeA)` to SubscriptionManager: bypasses current-mode constraint; marks subscription as PENDING-ModeA, activates on first ModeA transition. | subscription_manager.rs |
| **G2-M3** | IBKR Error 200 ("No security definition") — SubscriptionManager hangs waiting for ACK indefinitely | Add Error 200 handler: cancel pending ACK wait; log `SubscriptionFailed { ticker_id, error: "NoSecurityDefinition" }` WAL event; blacklist symbol in Redis for 24h. | ibkr_broker.rs + subscription_manager.rs |
| **G2-M7** / **G2-IN20** | `reqContractDetails` returns multiple messages; `contractDetailsEnd` event must be handled to detect completion — without it, partial contract list is processed silently | Add `contractDetailsEnd` handler: buffer results in `Vec<ContractDetails>` until event fires; only then expose complete contract list to caller. | ibkr_broker.rs |
| **G2-I15** | IBKR Error 1102 ("Restoring Data Connection") — engine should not send new subscriptions during restoration | Add `ibkr_restoring: AtomicBool` flag; set on Error 1102, clear on first successful tick after restoration. SubscriptionManager checks flag before submitting new `reqMktData`. | ibkr_broker.rs + subscription_manager.rs |
| **G2-I21** | Semaphore with 100 permits is idiomatic Tokio for ≤100 line constraint | Confirmed: `Arc<Semaphore>(100)` is the implementation for line budget constraint (already in SC-02). `acquire()` on subscribe, permit returned (via RAII guard) on `cancelMktData` ACK. | subscription_manager.rs |

**Updated Gate (Phase 11):** Same as above PLUS: shared Redis pacing token bucket verified (Rust + Python respect same limit); `pre_session_subscribe()` test verified; Error 200 blacklist confirmed; `contractDetailsEnd` test with fragmented mock response; `ibkr_restoring` flag test (Error 1102 → pause → resume).

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 20h | **Depends on**: Phase 11
*(+2h vs v20 for EOD spread cache, reverse split handling, XETRA auction randomization)*

**v21 Amendments:**

- **EOD spread cache (v21-FIX-4):** SmartRouter reads `calibration/eod_spread_cache.json` for direct equity spread estimates. Real-time snapshot only for Tier 1 assets (ADV > £500k) with 800ms timeout (raised from 200ms). Cache populated by Ouroboros step 3. Route logic: if `cached_direct_spread_bps < etp_spread_bps × 0.9` AND health passes → direct route; else ETP.
- **Reverse split symbology (G2-M1):** `symbology_mapper.py` handles reverse splits (1-for-10): adjust `total_shares /= split_factor`, `price_basis *= split_factor`. `SplitAdjustment` WAL event with `split_type: {Forward, Reverse}` and `ratio: f64`.
- **XETRA randomized closing auction (G2-M2):** XETRA unrosses randomly between 17:30:00 and 17:32:00 CET. Change XETRA T-5 hardcode from `15:25 UTC` to randomized window: subscribe `reqTradingHours` for XETRA; if within `[15:20 UTC, 15:32 UTC]` → treat as auction window.

**Deliverables (from v20, plus v21 amendments):**

- `smart_router.rs` (NEW):
  - ETP-first principle
  - **EOD spread cache lookup (v21-FIX-4):** `eod_spread_cache.json` read from calibration. Real-time snapshot at 800ms timeout only for Tier 1. Snapshot queue: max 5 concurrent.
  - Full cost model: FX drag + FTT + IBKR commission + stamp duty
  - Integer shares: `floor(kelly_gbp / lot_price_gbp)`
  - HKEX board lot ETP fallback
  - ETP 30-day tracking error check (>5% → demote)
  - FTT market cap ±10% hysteresis

- `isa_gate.rs` (NEW):
  - Hard-blocks: Taiwan, China, India — `HashSet<&'static str>`
  - ISA tax year boundary = April 6
  - ISA annual limit check

**Acceptance Tests (AT-19 to AT-40):**
*(+4 new tests vs v20 for EOD cache, reverse split, XETRA)*
- AT-19 through AT-36: same as v20
- **AT-37: EOD spread cache hit: illiquid ticker → no real-time snapshot, cache spread used for routing decision**
- **AT-38: Real-time snapshot: Tier 1 ticker (ADV > £500k) → 800ms timeout, not 200ms**
- **AT-39: Reverse split: CostBasisEntry with 100 shares at £50 → 1-for-10 → 10 shares at £500; SplitAdjustment WAL event logged**
- **AT-40: XETRA auction window: time within [15:20-15:32 UTC] → AuctionAvoidance veto fires**

**Gate**: 22 tests pass; EOD spread cache verified (no real-time snapshot for illiquid tickers); XETRA window verified; reverse split WAL event verified

**Additional Full Triage Amendments (Phase 12 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-F21** | Chandelier floor `1.5×spread×leverage` double-counts leverage for ETP spreads (ETP spread already incorporates leverage) | Fix: Chandelier floor for ETPs = `max(ATR_floor, 1.5 × etp_spread_pct)` only. Leverage multiplier applied ONLY when routing to direct underlying equity. | exit_engine.rs |
| **G2-R24** / **G2-IN8** | `tokio::timeout` cancels Rust future but NOT the IBKR snapshot request; orphaned subscriptions exhaust 100-line limit | After `tokio::timeout` fires: immediately call `cancelMktData(req_id)` to IBKR to close the orphaned subscription. Track `pending_snapshot_req_ids: HashSet<ReqId>`. | smart_router.rs |

**Updated Gate (Phase 12):** Same as above PLUS: Chandelier leverage double-count test (3x ETP: stop uses ETP spread only, not ×3); `cancelMktData` sent after timeout verified in mock; orphaned subscription count = 0 after 800ms timeout test.

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 24h | **Depends on**: Phase 12
*(+2h vs v20 for dynamic σ_noise, trend_velocity normalization, Kalman reset on gap)*

**v21 Amendments:**

- **Dynamic σ_noise per asset (v21-FIX-7):** `σ_noise = max(0.02, asset_30day_stddev_pct)` from `calibration/asset_volatility.json` (Ouroboros step 8). 3x ETPs with 8% daily vol get σ_noise=0.08; direct equities with 1.5% daily vol get σ_noise=0.02.
- **trend_velocity normalization (G2-M15):** Normalize raw `trend_velocity` input by dividing by asset's 30-day stddev before feeding into RotationScanner composite score. Prevents high-beta stocks monopolizing Bandit allocation.
- **Kalman covariance reset on gap (G2-M20):** If overnight gap > 2× ATR, reset Kalman filter covariance matrix `P` to prior `P_0`. Filter was heavily skewed by pre-gap state; reset allows rapid re-convergence post-gap.

**Deliverables (from v20, plus v21 amendments):**

- `hot_scanner.rs` (NEW): QuoteImbalance EWMA, CUSUM, Kalman, meta-label gate 0.55
  - **QuoteImbalance resume after QuoteImbalanceInvalidated:** after buffer clears (overflow_counter returns to 0 for ≥5 seconds), QI EWMA resumes from current state (not reset to zero)

- `rotation_scanner.rs` (NEW):
  - **Gaussian-Gaussian Thompson Sampler (v20-FIX-11):** dynamic σ_noise per asset **(v21-FIX-7)**
  - Prior: `μ_0 = 0.0`, `σ_0 = 0.05`, `σ_noise = asset_30day_stddev` (not static 0.03)
  - Hard slot limit: max 40 HotScanner + 10 RotationScanner = 50 total scanner lines
  - **trend_velocity normalization (v21):** `normalized_velocity = raw_velocity / asset_30day_stddev`
  - WAL attribution: `StrategyId::HotScanner` / `StrategyId::RotationScanner`

- `universe_scanner.rs` (NEW): ADV filter, RVOL calc, 100-line budget respect

**Acceptance Tests (AT-41 to AT-60):**
*(+3 new tests vs v20 for dynamic σ_noise, velocity normalization, Kalman reset)*
- AT-41 through AT-55: same as v20 (renumbered)
- **AT-56: Dynamic σ_noise: 3x ETP with 8% daily vol → σ_noise=0.08; direct equity 1.5% → σ_noise=0.02. TS allocates more lines to 3x ETP with higher mean PnL% despite volatility.**
- **AT-57: trend_velocity normalization: high-beta asset (30d stddev=5%) and low-beta (1%) with same raw velocity → equal normalized scores (neither monopolizes scanner)**
- **AT-58: Kalman covariance reset: overnight gap > 2×ATR → P reset to P_0; filter converges within 10 ticks post-gap (vs 50 ticks without reset)**

**Gate**: 18 tests pass; dynamic σ_noise loaded from calibration/asset_volatility.json; Gaussian-Gaussian TS verified with per-asset σ_noise; trend_velocity normalization verified

**Additional Full Triage Amendments (Phase 13 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-I1** | Thompson Sampler reward uses net PnL only — ignores slippage (high-slip instruments rated same as low-slip) | Add slippage cost to reward: `reward = net_pnl_pct - estimated_slippage_bps / 10000`. Ouroboros computes slippage from `fill_price vs mid_at_submission` from WAL `FillEvent`. | ouroboros/data_fetch.py step 8 + rotation_scanner.rs |

**Updated Gate (Phase 13):** Same as above PLUS: Thompson Sampler reward includes slippage; verify low-slippage instrument beats high-slippage instrument with same PnL in bandit allocation test.

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 22h | **Depends on**: Phase 13
*(unchanged from v20 — TWAP cancel on Chandelier hit added as new item)*

**v21 Amendment:**

- **Cancel TWAP slices on Chandelier stop hit (G2-M17):** If Chandelier trailing stop triggers during an active entry TWAP sequence, immediately cancel all remaining unfilled TWAP slices. Submit a single exit order at market. Do NOT complete the entry if the exit signal fires first.

**Deliverables (from v20, plus v21 amendment):**

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
*(+1 new test vs v20)*
- AT-61 through AT-74: same as v20 (renumbered)
- **AT-75: TWAP slice cancel: Chandelier stop triggers during 3rd of 5 TWAP slices → slices 4 and 5 cancelled, exit order submitted**

**Gate**: 15 tests pass; Chandelier-TWAP interaction verified; leverage-adjusted floor verified at 3x ETP

**Additional Full Triage Amendments (Phase 14 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-M25** | TWAP individual slice below exchange minimum order value → IBKR rejects silently | Add pre-submission check: `if slice_value_gbp < exchange_minimum_gbp[exchange] → aggregate remaining slices into single order`. Minimum thresholds in `exchange_profile.rs`. | executioner_v2.rs |
| **G2-I28** | TWAP with uniform slices when volume history < 5 days → poor execution quality | If `volume_history_days < 5`: use U-shape VWAP as default (not TWAP with uniform slices). VWAP uses the theoretical U-shape volume distribution until actual volume data accumulates. | executioner_v2.rs |

**Updated Gate (Phase 14):** Same as above PLUS: slice-below-exchange-minimum aggregation test (5-slice TWAP on £300 slice → aggregates into 1 order); new instrument VWAP default verified.

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 18h | **Depends on**: Phase 14
*(+1h vs v20 for Maillard domain check, CVaR-Kelly integration, VIX blind spot at startup)*

**v21 Amendments:**

- **Maillard (2012) CF domain check (v21-FIX-3):** `if N < 20 OR |S| >= 2 OR K <= S² - 1 → use Gaussian CVaR (not CF)`. All three conditions must be satisfied to use Cornish-Fisher. The K > S²-1 check is the new addition.
- **CVaR limit scaling with Kelly ramp (G2-M14):** When `kelly_scale < 1.0` (ramp active), CVaR limit also scales: `cvars_limit = base_cvars_limit × kelly_scale`. Prevents full-scale risk on sub-scale positions. At `kelly_scale=0.1`, CVaR limit is 10% of normal.
- **VIX circuit breaker blind at startup (G2-F34):** Initialize `vix_5min_history` with current VIX reading on startup (not empty). If only 1 reading → skip circuit breaker check until 5 minutes of history accumulated. Log `VixHistoryInsufficient` event on startup.
- **CVaR max-correlation false ORANGE trigger (G2-R14):** When VIX circuit breaker fires and all ρ=1.0, apply a damping factor: `portfolio_heat_limit × 0.8` (not 0.0). Prevents automatic ORANGE liquidation on the first crash pulse.

**Deliverables (from v20, plus v21 amendments):**

- `risk_arbiter.rs` EXTENDED — 31 vetoes (9 new + 22 existing)
- `cvar_heat.rs` (NEW):
  - **CF expansion gated: N≥20 AND |S|<2 AND K > S²-1 (v21-FIX-3)**
  - CVaR limit scales with `kelly_scale` **(v21)**
  - VIX circuit breaker with startup blind spot protection **(v21)**
  - CVaR max-correlation damping factor 0.8 **(v21)**
- Dynamic Kelly ramp: `kelly_scale = max(0.1, min(1.0, validated_trades / 250.0))`

**Acceptance Tests (AT-76 to AT-97):**
*(+3 new tests vs v20)*
- AT-76 through AT-92: same as v20 (renumbered)
- **AT-93: Maillard K>S²-1 check: K=0.1, S=0.5 → S²-1 = -0.75; K=0.1 > -0.75 → CF allowed; K=0.1, S=1.5 → S²-1 = 1.25; K=0.1 NOT > 1.25 → Gaussian fallback**
- **AT-94: CVaR-Kelly scaling: kelly_scale=0.1 → CVaR limit = 10% of base; kelly_scale=0.5 → CVaR limit = 50% of base**
- **AT-95: VIX blind at startup: first 4 minutes of data → VixHistoryInsufficient logged; circuit breaker NOT evaluated; no false trips**

**Gate**: 20 tests pass; 31 total vetoes confirmed; Maillard domain check verified at CF/Gaussian boundary; CVaR-Kelly scaling verified; VIX blind spot at startup handled

**Additional Full Triage Amendments (Phase 15 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-F32** | `MINIMUM_ENTRY_GBP` suspended during Kelly ramp — IBKR exchange minimum may still reject (Error 2109) | Add explicit handler for IBKR exchange minimum rejection: log `WalPayload::SubmissionFailedMinimum { ticker_id, attempted_gbp, exchange_minimum_gbp }`. Do NOT retry. | ibkr_broker.rs + types/wal.rs |
| **G2-M21** | VIX data feed failure — no proxy fallback; circuit breaker disabled | Add VIX fallback chain: Polygon VIX → IBKR VX continuous contract (`reqMktData` for VX) → VVIX as vol-of-vol proxy. If all three fail: use `last_known_vix + 5%` safety buffer; log `VixFeedFailed` WAL event. | cvar_heat.rs + ibkr_broker.rs |

**Updated Gate (Phase 15):** Same as above PLUS: IBKR exchange minimum rejection handled (SubmissionFailedMinimum WAL event); VIX fallback chain tested (mock all three sources failing → last-known +5% used).

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 24h | **Depends on**: Phase 15
*(+2h vs v20 for Polygon timezone normalization, EOD spread cache, asset_volatility.json, Python asyncio loop fix)*

**v21 Amendments:**

- **Polygon timezone normalization (v21-FIX-8):** In Ouroboros step 2, after fetching corp actions from Polygon, run: `ex_date_london = datetime.fromisoformat(polygon_date).astimezone(ZoneInfo("Europe/London")).date()` for ALL entries. Use `ex_date_london` in `corp_action_blocklist.json`.
- **EOD spread cache (v21-FIX-4 support):** Ouroboros step 3 (universe discovery) writes `calibration/eod_spread_cache.json`: for each ticker in 5,000+ universe, record `{"ticker": "ASML.NA", "spread_bps": 12.3, "adv_gbp": 850000, "tier": 1}`. Used by SmartRouter as primary spread data source.
- **asset_volatility.json (v21-FIX-7 support):** Ouroboros step 8 (Thompson Sampling update) computes 30-day rolling stddev per asset from WAL outcomes. Writes `calibration/asset_volatility.json`: `{"ASML.NA": 0.018, "QQQ3.L": 0.085, ...}`. Loaded by RotationScanner at artifact load time.
- **Python asyncio loop fix (G2-IN6):** `telegram_reporter.py` outer `while True` must catch `RuntimeError: Event loop is closed` separately from network errors. On `RuntimeError` → create fresh `asyncio.new_event_loop()` before restarting. The existing `except Exception: backoff()` pattern swallows this and loops infinitely on a dead loop.

**Deliverables:**

- `ouroboros/` EXTENDED — 10-step pipeline:
  1. **Data fetch** — Polygon.io + IBKR active tickers; nightly cost basis clear + reqPositions resync
  2. **Corporate action blocklist** — Polygon.io; **normalise ALL dates to Europe/London timezone (v21-FIX-8)**; atomic write
  3. **Universe discovery** — 5,000+ tickers; write **EOD spread cache** `eod_spread_cache.json` **(v21-FIX-4 support)**; Polars LazyFrame 500-ticker batches
  4. **Feature engineering** — Polars LazyFrame; write to /dev/shm during processing
  5. **Scoring** — ASER: momentum 30%, liquidity 20%, volatility 20%, regime 15%, recency 15%
  6. **Meta-label training** — Logistic Regression / LightGBM fallback
  7. **Chandelier calibration** — ATR, MAE/MFE profiling
  8. **Thompson Sampling update** — Gaussian-Gaussian posteriors; write **asset_volatility.json** **(v21-FIX-7 support)**
  9. **DCC-GARCH update** — cross-asset correlation matrix; write `calibration/asia_cross_tz.json` with `updated_at` timestamp
  10. **PDF generation + artifact write + Telegram 🟡 ALIVE** — daily summary report; active_state.wal write (v21-FIX-9 support)

- Sequential step enforcement, Polars mandate, atomic blocklist write, Parquet cleanup — all retained from v20

**Acceptance Tests (AT-98 to AT-118):**
*(+3 new tests vs v20)*
- AT-98 through AT-110: same as v20 (renumbered)
- **AT-111: Polygon corp action date normalisation: Polygon returns '2026-04-05T04:00:00-05:00' (US EDT) → normalised to '2026-04-05' Europe/London → NOT '2026-04-06' (which naive UTC conversion would give)**
- **AT-112: EOD spread cache: eod_spread_cache.json present after step 3; contains spread_bps and tier fields for each ticker**
- **AT-113: asset_volatility.json: present after step 8; QQQ3.L stddev > 0.05 (3x ETP); ASML.AS stddev < 0.03 (unleveraged large-cap)**

**Gate**: 21 tests pass; Polygon timezone normalisation verified with US EDT test date; EOD spread cache verified non-empty; asset_volatility.json verified with expected tier differentiation

**Additional Full Triage Amendments (Phase 16 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-R7** | `resource.getrlimit(RLIMIT_AS)` checks OS virtual limit, not free RAM — pre-flight check is meaningless | Replace with `psutil.virtual_memory().available` check against configured `MIN_FREE_RAM_GB: 1.5` threshold before Ouroboros starts. Log `InsufficientRAM` WAL event if below threshold. | ouroboros/data_fetch.py step 0 |
| **G2-R28** | DCC-GARCH Monday 26h staleness → false "weights stale" alert every Monday (Friday night → Monday open = 72h gap) | Fix staleness threshold: `max_stale_hours = 36 if weekday == Monday else 12`. Prevents false Monday alerts. | ouroboros/data_fetch.py step 9 |
| **G2-F33** / **G2-IN9** | `os.replace()` (rename) fails across different mount points (`/tmp` → EBS) with `OSError: Invalid cross-device link` | Write temp file to same directory as destination: `dest.parent / (dest.name + '.tmp')` → write → `os.replace(tmp, dest)`. Applies to all Ouroboros atomic writes. | ouroboros/data_fetch.py (all atomic writes) |
| **G2-I3** | Ouroboros loads full Parquet then filters — `scan_parquet()` with predicate pushdown skips unneeded row groups at storage layer | Replace `read_parquet() + filter` with `scan_parquet().filter().collect()` in all Ouroboros universe discovery steps. 60-80% IO reduction. | ouroboros/data_fetch.py step 3 |
| **G2-I12** | Polygon corporate action coverage for European ETPs is incomplete — yfinance provides free secondary source | Add `yfinance.Ticker(symbol).actions` as fallback in Ouroboros Step 2 when Polygon returns empty response or 502 after 3 retries. Cross-validate ex-dates. | ouroboros/data_fetch.py step 2 |
| **G2-I30** | No Polars query plan logging — debugging requires full rerun | Add `logging.debug(lf.explain(optimized=True))` before each major Ouroboros LazyFrame `.collect()`. Debug-level only (no production overhead). | ouroboros/data_fetch.py |

**Updated Gate (Phase 16):** Same as above PLUS: `psutil` RAM check verified; Monday DCC-GARCH 36h threshold tested; all atomic writes use same-directory temp file; `scan_parquet()` predicate pushdown verified (IO halved on test); yfinance fallback verified on mock 502; Polars explain debug log present.

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 15h | **Depends on**: Phase 16
*(+1h vs v20 for asyncio RuntimeError fix, Redis heartbeat async client)*

**v21 Amendments:**

- **Redis heartbeat async client (G2-IN2):** `aegis_heartbeat_ts` write in `engine.rs` must use **async Redis client** (not synchronous). Synchronous Redis call blocks Tokio thread every 30 minutes, causing latency spikes on the critical tick path. Use `redis::aio::ConnectionManager` or `deadpool-redis`.
- **Telegram bot authorization (G2-M28):** Long-polling HALT command must check that the sender's `chat_id` matches the authorized `TELEGRAM_AUTHORIZED_CHAT_ID` from config. Reject HALT from unknown chat IDs with logged `UnauthorizedHaltAttempt` event.
- **asyncio RuntimeError fix (G2-IN6):** `telegram_reporter.py` outer loop:
  ```python
  while True:
      try:
          loop = asyncio.new_event_loop()
          asyncio.set_event_loop(loop)
          loop.run_until_complete(app.run_polling())
      except RuntimeError as e:
          if "Event loop is closed" in str(e):
              time.sleep(5)  # create fresh loop on next iteration
          else:
              raise
      except Exception:
          time.sleep(backoff())
  ```

**Deliverables (from v20, plus v21 amendments):**

- Engine-side heartbeat via async Redis client (not blocking) **(v21)**
- `telegram_reporter.py`: authorized chat_id check on HALT commands **(v21)**
- asyncio RuntimeError safe restart loop **(v21)**
- All v20 deliverables retained (infinite retry loop, daemon=False, shadow book £50 threshold, PDF generation)

**Acceptance Tests (AT-119 to AT-133):**
*(+2 new tests vs v20)*
- AT-119 through AT-127: same as v20 (renumbered)
- **AT-128: Redis heartbeat via async client: no blocking call in Tokio thread (verify via latency measurement: tick processing latency unchanged during SETEX write)**
- **AT-129: Unauthorized HALT: send HALT from unknown chat_id → rejected, UnauthorizedHaltAttempt WAL event logged**

**Gate**: 15 tests pass; async Redis heartbeat verified; HALT authorization verified; asyncio RuntimeError recovery verified

**Additional Full Triage Amendments (Phase 17 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-M13** | Telegram long-polling keep-alive — API disconnects after ~60s inactivity; without infinite retry, polling thread exits | Wrap Telegram polling in `while True: try: poll() except: sleep(backoff); continue`. Exponential backoff capped at 60s. (This is already in the v21 asyncio fix; verify the retry loop is present.) | telegram_reporter.py |
| **G2-M18** | Telegram API 429 (Too Many Requests) during ORANGE liquidation — multiple simultaneous alerts exceed 20 msg/min limit | Implement Telegram send queue with rate limiting: max 20 messages/minute per chat. Priority queue: HALT/ORANGE emergency messages bypass rate limit. Exponential backoff on 429. | telegram_reporter.py |
| **G2-I29** | Shadow Book tracks divergence but has no counterfactual metric ("RiskGate saved/cost £X") | Add `shadow_counterfactual_gbp` field to WAL `VetoEvent` payload: compute from price move since veto time to next close. Daily PDF report includes "Risk Gate value: £X saved/cost". | shadow_book.py + types/wal.rs |
| **G2-IN25** | PyMuPDF creates temp files in `/tmp` — Docker `/tmp` may be restricted to 64MB; PDF generation fails silently | Write PyMuPDF output directly to `/data/reports/` (EBS volume). No `/tmp` usage for PDF generation. | pdf_generator.py |

**Updated Gate (Phase 17):** Same as above PLUS: Telegram 429 rate limiter verified (20 msg/min cap); HALT message bypasses rate limit; `shadow_counterfactual_gbp` in VetoEvent WAL; PyMuPDF writes to `/data/reports/` (not `/tmp`).

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 21h | **Depends on**: Phase 17
*(+1h vs v20 for UK stamp duty on MTF routing, Ouroboros retry on Polygon 502)*

**v21 Amendments:**

- **UK stamp duty on European MTFs (G2-M11):** UK stamp duty (50 bps) applies based on ISIN, regardless of execution exchange. If UK-ISIN equity routed to Cboe Europe or Turquoise (MTF) to avoid LSE stamp duty → stamp duty still applies. Add `isin_prefix_to_stamp_duty_jurisdiction` map: `"GB" → UK_STAMP_50_BPS`.
- **Ouroboros step 2 retry on Polygon 502 (G2-F13):** wrap Polygon `/v3/reference/dividends` and `/v3/reference/splits` calls in retry loop: max 3 attempts, exponential backoff (2s, 4s, 8s). If all 3 fail → log `CorpActionFetchFailed` WAL event + Telegram alert, but do NOT abort pipeline (step 2 proceeds with last successful blocklist).

**Deliverables (from v20, plus v21 amendments):**

- `transaction_tax.rs` (NEW): integer bps storage, per-exchange stamp duty, FTT no-carry flag
  - **UK ISIN-based stamp duty (v21):** `isin_jurisdiction_stamp_duty_bps`: `{"GB": 50, "IE": 100, "FR": 0, ...}`. Applied regardless of routing exchange.
- `currency.rs` (NEW): IDEALPRO routing enforced, 6 currencies, stale-rate detection
- `exchange_profile.rs` (NEW): 15 European exchange profiles + XETRA randomized auction window **(v21)**
- `sub_universe_allocator.rs` (NEW): VPIN NaN guard

**Acceptance Tests (AT-134 to AT-157):**
*(+3 new tests vs v20)*
- AT-134 through AT-150: same as v20 (renumbered)
- **AT-151: UK ISIN stamp duty on MTF: ISIN=GB0009252882 (Vodafone) routed to Cboe Europe → 50 bps stamp duty applied (ISIN-based, not exchange-based)**
- **AT-152: Polygon 502 retry: simulate 2 failures + 1 success → step 2 completes, 2 retries logged**
- **AT-153: Polygon 502 all 3 fail: CorpActionFetchFailed WAL event + Telegram alert; pipeline continues with stale blocklist (not aborted)**

**Gate**: 25 tests pass; UK ISIN stamp duty on MTF routing verified; Polygon retry verified with mock 502; 5 paper trading days with European tickers active

**Additional Full Triage Amendments (Phase 18 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-F17** | Carry allocator assumes all carry positions use 2 lines (ETP requires underlying feed); direct equity carries use 1 | Fix allocator: query `subscription_type` per carry position; `line_weight = if direct_equity { 1 } else { 2 }`. Formula: `available = 100 − Σ(carry_positions × line_weight_i) − active_scanner`. | overnight_carry.rs / subscription_manager.rs |
| **G2-M2** | XETRA randomised closing auction (17:30-17:32 CET): T-5 hardcode misses 7-minute randomisation window | Adjust XETRA T-7: flatten from 17:23 CET to 17:30 CET (not T-5 from 17:25). Maintain position through 17:23, then begin flattening. | exchange_profile.rs (XETRA profile) |

**Updated Gate (Phase 18):** Same as above PLUS: direct equity carry line weight = 1 (not 2) verified; XETRA T-7 flatten window verified against randomised auction schedule.

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21h | **Depends on**: Phase 18
*(+1h vs v20 for JPY decimal precision, IBKR reconnect client_id clear)*

**v21 Amendments:**

- **JPY decimal precision (G2-M16):** All price arithmetic for JPY-denominated instruments must use `0` decimal places: `price.round() as u64` (not `f32` — f32 degrades at 5 digits). Use `f64` for all Asian currency arithmetic. Rust: `(price_jpy * 100.0).round() / 100.0` then floor to 0 decimal places for order submission.
- **IBKR reconnect client_id cleared (G2-IN13):** Before reconnecting after 04:45 UTC GW restart, wait 15s after disconnect before attempting with `client_id=101`. IBKR server needs time to clear the previous session. Attempts within 15s of disconnect → `ConnectionRefused` loop. First reconnect attempt at T+15s, subsequent at exponential backoff.

**Deliverables (from v20, plus v21 amendments):**

- `asian_exchange.rs` (NEW): 6 exchange profiles + ASX DST dynamic + KRX VI confirmation
  - **JPY 0-decimal precision (v21)**
- `clock.rs` EXTENDED: 04:45 UTC reconnect handler with 15s initial delay **(v21)**

**Acceptance Tests (AT-158 to AT-178):**
*(+2 new tests vs v20)*
- AT-158 through AT-171: same as v20 (renumbered)
- **AT-172: JPY order price: raw price 7823.47 → submit as 7823 (0 decimal places); no IBKR price format rejection**
- **AT-173: IBKR reconnect 15s delay: simulate disconnect → attempt at T+5s rejected; attempt at T+17s succeeds**

**Gate**: 20 tests pass; JPY decimal precision verified; ASX DST dynamic verified; reconnect 15s delay verified

**Additional Full Triage Amendments (Phase 19 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-F23** | ES futures front-month hardcoded — flatlines on expiration week (quarterly roll: March, June, September, December) | Add auto-roll logic: query `reqContractDetails` for ES futures; select contract with highest open interest. Update continuously; roll 3 days before expiration. | asian_exchange.rs + ibkr_broker.rs |
| **G2-IN24** | Korean Won (KRW) arithmetic: ₩150,000,000 lot values risk `f32` precision loss | Audit all `f32` price/value fields in `MarketTick`, `PositionState`, `CostBasisEntry` for KRW instruments; enforce `f64` throughout. Order submission: `price_krw.round() as u64`. | types/market_tick.rs + portfolio.rs |

**Updated Gate (Phase 19):** Same as above PLUS: ES futures front-month roll verified (mock expiration week → automatically selects next contract); KRW f64 audit: `grep -r "f32" --include="*.rs" | grep -v test` returns zero KRW-related hits.

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 23h | **Depends on**: Phase 19
*(+1h vs v20 for CarryMonitor HashSet whitelist, reqPnL 3-min blind spot)*

**v21 Amendments:**

- **CarryMonitor HashSet whitelist (v21-FIX-10):** `authorized_carry_conids: HashSet<ConId>`. Populated on `try_carry()` success. Cleared on position close. Any `account_pnl_update` with conid NOT in set → silently discard, increment `discarded_pnl_updates_count`. Report count in Telegram daily summary.
- **reqPnL 3-min update interval (G2-IN15):** IBKR pushes `account_pnl` updates roughly every 3 minutes, not continuously. `CarryMonitor` must NOT assume gap-down event between updates. Add `last_pnl_update_ts` tracking; if no update for > 5 minutes → `PnLStreamStale` WAL event + Telegram alert (but do NOT assume gap-down).

**Deliverables (from v20, plus v21 amendments):**

- `overnight_carry.rs` (NEW): full state machine
  - **CarryMonitor HashSet whitelist (v21-FIX-10)**
  - **reqPnL staleness detection (v21)**
  - Account-level reqPnL + FTT no-carry enforcement

**Acceptance Tests (AT-179 to AT-203):**
*(+2 new tests vs v20)*
- AT-179 through AT-195: same as v20 (renumbered)
- **AT-196: Unauthorized PnL update: Vanguard ETF conid not in authorized set → discarded silently, discarded_count incremented**
- **AT-197: PnL stream stale: 6 minutes without reqPnL update → PnLStreamStale WAL event + Telegram alert; no gap-down assumed**

**Gate**: 24 tests pass; HashSet whitelist verified with manual Vanguard ETF conid injection; PnL staleness detection verified

**Additional Full Triage Amendments (Phase 20 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-IN15** | `reqPnL` pushes updates every ~3 minutes — 3-minute gap creates temporal blind spot (3x ETP can move 5% in 3 min) | Supplement with real-time tick-based P&L: `unrealized_pnl = (last_price - cost_basis) × quantity`. Updated on every tick from existing market data subscriptions. `reqPnL` remains as reconciliation check only. | overnight_carry.rs + engine.rs |
| **G2-I7** | Carry allocator uses estimated line count from `carry_count × 2` — should read live `active_line_count` from AtomicUsize | Implement `SubscriptionManager::available_lines() -> usize` method; carry allocator calls this directly instead of computing from carry position count. | subscription_manager.rs + overnight_carry.rs |

**Updated Gate (Phase 20):** Same as above PLUS: real-time tick-based P&L verified (gap-down simulated → engine detects within 1 tick, not 3 minutes); `SubscriptionManager::available_lines()` verified returns correct count after multiple subscribe/cancel operations.

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13h | **Depends on**: Phase 20
*(unchanged from v20)*

*(No original v21 amendments — Phase 21 spec complete as-is; full triage adds 3 items)*

**Additional Full Triage Amendments (Phase 21 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-M4** | Polars NaN/Null in one return series → full row/column NaN in DCC-GARCH covariance matrix → NaN CVaR heat → RiskGate bypassed | Add null check before DCC-GARCH: `df = df.fill_nan(0.0)`; drop instruments with >5% null returns from covariance universe. Log `CovarianceNullDrop { ticker_id, null_pct }` WAL event. | cross_timezone.py + cvar_heat.rs |
| **G2-M9** | ASX closes at ~06:00 UTC; Nikkei at ~06:30 UTC; S&P at 21:00 UTC — unaligned timestamps produce spurious cross-lag correlations | Standardise all daily returns to 21:00 UTC close for DCC-GARCH input. Use last available price at or before 21:00 UTC per instrument. | cross_timezone.py |
| **G2-IN21** | DCC-GARCH matrix inversion panics inside `Arc<RwLock>` write guard → lock poisoned → DCC-GARCH permanently unavailable | Wrap matrix inversion in `std::panic::catch_unwind`; on failure: release lock, substitute identity matrix (ρ=0 correlations, maximum diversification). Log `CovarianceFallback` WAL event. | cvar_heat.rs |

**Deliverables**: Same as v20 Phase 21 PLUS above 3 amendments.

**Acceptance Tests (AT-204 to AT-215 + 3 new):**
- **AT-213: Covariance NaN guard: inject NaN column for one ticker → excluded from covariance; CovarianceNullDrop WAL event logged**
- **AT-214: 21:00 UTC timestamp alignment: ASX ticker last price before 21:00 UTC used for Monday DCC-GARCH (not post-21:00 close)**
- **AT-215: Matrix inversion panic: synthetic perfectly-correlated pair → `catch_unwind` triggers → identity matrix substituted; lock NOT poisoned; trading continues**

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 30h | **Depends on**: Phase 21
*(+2h vs v20 for active_state.wal, ArcSwap exchange-hours safety, PDF report cleanup)*

**v21 Amendments:**

- **active_state.wal nightly rewrite (v21-FIX-9):** Ouroboros step 10 writes `calibration/active_state.wal`: JSON with all open positions, cost basis, chandelier state, timestamp. Engine startup: if `active_state.wal` present AND < 25h old → fast-path load open positions from it, skip historical WAL replay for position reconstruction. Full WAL replay still runs for reconciliation audit (parallel fast path).
- **ArcSwap exchange-hours safety (G2-R5/IN3):** SIGHUP config reload MUST validate that no new `european_exchange_profiles.toml` changes would cause open positions to immediately violate `ExchangeClosed` veto. Validation: for each open position, check if the new config marks their exchange as closed. If any violation detected → reject config reload, Telegram `ConfigReloadRejected` alert.
- **PDF report cleanup cron (G2-M12):** Add to Supercronic: daily at 03:00 UTC: `find /tmp -name "aegis_daily_*.pdf" -mtime +30 -delete`. Prevents 60+ PDF accumulation on disk.

**Deliverables (from v20, plus v21 amendments):**

- **active_state.wal rewrite** as described above **(v21-FIX-9)**
- **ArcSwap exchange-hours validation** on SIGHUP **(v21)**
- **PDF cleanup cron** **(v21)**
- All v20 deliverables retained (SIGTERM drill, WAL compaction open-position exclusion, NTP check, chaos suite, Prometheus localhost, rate limiter audit)

**Acceptance Tests (AT-216 to AT-238):**
*(+3 new tests vs v20)*
- AT-216 through AT-226: same as v20 (renumbered)
- **AT-227: active_state.wal fast-path: engine restart with active_state.wal < 25h old → positions loaded from active_state.wal in <100ms; no historical WAL parse**
- **AT-228: active_state.wal stale: restart with active_state.wal > 25h old → falls back to historical WAL replay; logs ActiveStateStale**
- **AT-229: SIGHUP config reload: new config closes exchange with open position → reload rejected, ConfigReloadRejected logged; old config retained**
- **AT-230: PDF cleanup cron: after 35 days, PDFs older than 30 days removed; newest 30 PDFs retained**

**Gate**: 22 tests pass; 48h continuous paper run without HALT; active_state.wal fast-path startup verified (< 100ms); ArcSwap exchange-hours safety verified; PDF cleanup verified

**Additional Full Triage Amendments (Phase 22 — from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md):**

| ID | Finding | Action | File |
|----|---------|--------|------|
| **G2-R32** | IBKR Code 2 (Delayed Data Warning) triggers system-wide signal blackout — too aggressive; 1 ticker's delay shouldn't halt all 100 | Per-ticker Code 2 blackout only: block signal generation for specific ticker that received Code 2; allow all other tickers to continue. Clear on next Code 1 (live data confirmed) for that ticker. | ibkr_broker.rs + engine.rs |
| **G2-IN26** | 48h Paper Shadow Run WAL can exceed 15GB — tick events should NOT be written to persistent WAL | Separate WAL from tick stream: only write trade-lifecycle events (`PositionOpened`, `FillEvent`, `PositionClosed`, `VetoEvent`) to persistent WAL. Tick data: time-limited ring buffer in `/dev/shm` only (not persisted). | wal_writer.rs + engine.rs |
| **G2-I11** | Kalman/CUSUM filter state lost on restart — learned microstructure state reset to prior | Add `KalmanState { mean: f64, variance: f64, last_updated_ts: u64 }` to `WalPayload` enum; write on SIGTERM before shutdown. Restore from WAL on startup if state age < 24h. | types/wal.rs + hot_scanner.rs |
| **G2-M10** | If EBS volume full at SIGTERM, `SystemShutdown` WAL event cannot be written — next startup cannot distinguish clean shutdown from crash | EBS free-space pre-check at SIGTERM: `if disk_free_bytes < 100MB → write SystemShutdown to /dev/shm as backup`. On startup: check both EBS WAL and `/dev/shm` fallback for shutdown marker. | main.rs (SIGTERM handler) |

**Updated Gate (Phase 22):** Same as above PLUS: Code 2 per-ticker blackout test (1 ticker Code 2 → only that ticker paused; 99 others continue); WAL tick separation test (tick data NOT in WAL file after 1h paper run); KalmanState WAL roundtrip test (shutdown → restart → state restored, age < 24h); EBS-full SIGTERM test (inject disk-full condition → SystemShutdown written to /dev/shm).

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(Romano-Wolf single-hypothesis correction retained from v20)*

> **The Engineering-vs-Alpha Boundary (Gemini Institutional Syndicate, confirmed):**
> Phases 8-22 guarantee the chassis. They eliminate every known infrastructure failure mode: corrupted execution timing, broken data feeds, WAL corruption on restart, IBKR API misuse, clock bugs, OOM crashes. The 0% win rate on 52 prior paper trades is attributable to these infrastructure failures — not to signal quality. Phase 23 is where that hypothesis is tested.
>
> **If WR ≥ 40% and Sharpe > 0:** The underlying S15 signal math has genuine edge. Live capital is granted.
> **If WR < 40% or Sharpe ≤ 0:** The infrastructure was not the only problem. The signal generation logic must be rewritten before live capital is deployed. The Crucible will reject it before a single real pound is lost.
>
> This is the power of the framework. Engineering guarantees survival. Phase 23 determines whether there is alpha.

**Deliverables (7 test suites):**

1. **Suite 1 — Trade Gate**
   - WR ≥ 40% on last 100 paper trades
   - Single-hypothesis t-test (N=1): `t-stat = mean_pnl / (std_pnl / sqrt(100))` ≥ 2.0 (two-tailed, df=99)
   - Bootstrap resampling: 1,000 iterations, 95th percentile CI on WR and Sharpe
   - Sharpe (cost-adjusted) > 0
   - Zero HALT events triggered by system errors (infrastructure failures disqualify the run)
   - Max drawdown < 8%

2. **Suite 2 — SIGTERM Flatten Drill**
   - Kill container mid-position (3 open positions)
   - Flat on restart, WAL consistent, no orphans
   - Repeat 5 times

3. **Suite 3 — 48h Paper Shadow Run**
   - Shadow book vs broker: max divergence < £50 at any point
   - All mode transitions logged with latency < 50ms

4. **Suite 4 — Chaos Engineering**
   - Python bridge crash, IBKR kill, Redis kill — all recovered in sequence

5. **Suite 5 — ISA Compliance Audit**
   - 200 synthetic order intents; 0 short orders, 0 Taiwan/China/India; 0 exceeding £20k
   - WAL `CorporateActionVeto` event fires for synthetic blocklist ticker
   - `isa_compliance_audit.json` generated

6. **Suite 6 — Line Budget Stress Test**
   - proptest 1,000 sequences; `active_line_count <= 100` invariant NEVER violated
   - Scanner Conservation Rule holds; HotScanner/RotationScanner → 0 underlying lines

7. **Suite 7 — Full Mode Cycle**
   - 24h paper run: ModeA → DARK → ModeB → ModeB+ → ModeC → DARK
   - DST boundary handled (chrono-tz verified)
   - NZX pre-subscribed at 22:55 UTC
   - Ouroboros completes all 10 steps within DARK

**Gate**: All 7 suites pass with written sign-off. 100 validated paper trades. No P0 bugs open. **APPROVED FOR LIVE CAPITAL** stamp.

> **Signal Rewrite Protocol (if Crucible fails):** If WR < 40% after a clean 100-trade Crucible run with zero infrastructure HALT events, the signal generation (HotScanner, RotationScanner, CUSUM thresholds, QuoteImbalance decay constants) is rewritten and the Crucible is rerun. The infrastructure (Phases 8-22) is NOT rebuilt — it is confirmed clean. Only the signal math is under revision.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE ✓** | 147+ (all passing) |
| **8** | Pre-Conditions + P0 (SC-01→SC-20 incl. v21 + triage fixes) | **48h** | **NEXT** | Unit tests per SC item |
| **11** | 5-Mode Clock + SubscriptionManager (full triage: pacing, Error 200, contractDetailsEnd) | **26h** | NOT STARTED | AT-01→23 |
| **12** | Smart Router + EOD cache + XETRA + Chandelier leverage fix + cancelMktData | **22h** | NOT STARTED | AT-24→46 |
| **13** | HotScanner + RotationScanner (dynamic σ_noise + velocity norm + slippage reward) | **25h** | NOT STARTED | AT-47→65 |
| **14** | Infinite Chandelier + Executioner V2 (slice minimum + VWAP fallback) | **24h** | NOT STARTED | AT-66→82 |
| **15** | RiskGate 31 Vetoes + CVaR (Maillard + Kelly scaling + VIX fallback + exchange minimum) | **21h** | NOT STARTED | AT-83→105 |
| **16** | Ouroboros (Polygon TZ, EOD/intraday cache, asset_volatility, psutil, os.replace, scan_parquet) | **28h** | NOT STARTED | AT-106→127 |
| **17** | Telemetry (async Redis, HALT auth, asyncio fix, Telegram 429, PyMuPDF EBS, counterfactual) | **19h** | NOT STARTED | AT-128→144 |
| **18** | European Equities + UK ISIN + carry line weight + XETRA T-7 | **23h** | NOT STARTED | AT-145→170 (+5 paper days) |
| **19** | Asia-Pac MODE A + JPY/KRW precision + reconnect delay + ES auto-roll | **24h** | NOT STARTED | AT-171→188 |
| **20** | Carry State Machine (HashSet + PnL staleness + tick P&L + available_lines()) | **26h** | NOT STARTED | AT-189→208 |
| **21** | Cross-Timezone Intelligence (NaN guard + 21:00 UTC alignment + matrix catch_unwind) | **16h** | NOT STARTED | AT-209→221 (+5 paper days) |
| **22** | Institutional Hardening (active_state.wal + ArcSwap + Code 2 per-ticker + WAL separation + KalmanState) | **36h** | NOT STARTED | AT-222→242 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **378h** | | **~250+ acceptance tests** |

*(+15h vs v20 for core v21 fixes; +12h for full triage additional amendments (SC-18/19/20, Phase 11-22 additional items) = 349h total)*

**At 20h/week**: ~17.5 weeks to live capital
**At 40h/week**: ~8.7 weeks to live capital

---

### Drawdown Tier Reference

| Tier | Kelly Sizing | New Entries | Existing Positions | Trigger |
|------|-------------|-------------|-------------------|---------|
| NORMAL | `kelly_scale × 100%` | ✓ Allowed | Managed normally | Default |
| **YELLOW** | `kelly_scale × 50%` | ✗ Blocked | Managed normally (exits still fire) | Ouroboros failure; drawdown −3% |
| **ORANGE** | 0% | ✗ Blocked | Close all positions at market | Drawdown −5% |
| **RED** | 0% | ✗ Blocked | Full halt (no exits, no orders) | Drawdown −8%; manual RESUME only |

*`kelly_scale = max(0.1, min(1.0, validated_trades / 250))` — ramps from 0.1× at 0 trades to 1.0× at 250 trades. CVaR limit also scales with kelly_scale. Yellow halves whatever the ramp currently produces.*

---

### New Files Created in Phases 8-23
*(same as v20 list, plus additions)*

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — RwLock + Semaphore (not Mutex)
├── mode_controller.rs         (Phase 11) — chrono-tz, no reqOpenOrders
├── smart_router.rs            (Phase 12) — EOD spread cache, 800ms timeout
├── isa_gate.rs                (Phase 12) — April 6 boundary
├── hot_scanner.rs             (Phase 13) — QuoteImbalance EWMA, QI resume
├── rotation_scanner.rs        (Phase 13) — Gaussian-Gaussian TS, dynamic σ_noise
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — TWAP cancel on Chandelier hit
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — Maillard K>S²-1, CVaR-Kelly scaling
├── overnight_carry.rs         (Phase 20) — HashSet<conid> whitelist
├── currency.rs                (Phase 18) — IDEALPRO routing
├── exchange_profile.rs        (Phase 18) — XETRA random auction window
├── transaction_tax.rs         (Phase 18) — UK ISIN stamp duty
├── sub_universe_allocator.rs  (Phase 18) — VPIN NaN guard
└── asian_exchange.rs          (Phase 19) — JPY 0-decimal precision

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — sequential steps, Polygon TZ normalise
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
├── corp_action_blocklist.json (Ouroboros step 2 — dates in Europe/London)
├── eod_spread_cache.json      (Ouroboros step 3 — NEW v21)
├── asset_volatility.json      (Ouroboros step 8 — NEW v21)
├── exchange_times.json        (Ouroboros step 1 — dynamic DST)
├── active_state.wal           (Ouroboros step 10 — NEW v21)
└── compaction_manifest.json   (Phase 22)
```

---

## TDD MANDATE (NON-NEGOTIABLE)

> Confirmed by Gemini Institutional Syndicate: *"Hold the agent strictly to Test-Driven Development (TDD) rules. Do not let it advance to Phase 11 until all 17 structural foundation items in Phase 8 are coded, tested, and verified."*

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
Begin Phase 8 of AEGIS_MASTER_PLAN_v21.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v21.md

TDD MANDATE: For each SC item — write the test first (failing), implement, run cargo test (passing), THEN move to the next. Never batch tests. Never advance without a green test. This is non-negotiable.

Implement all 17 SC items in order. Write unit tests for each. Run cargo test after each SC item before proceeding to the next.

SC-01: SIGTERM handler in main.rs — ctrlc crate, flatten positions → wait 30s for fills → write SystemShutdown WAL event → exit
SC-01a: docker-compose.yml — add `stop_grace_period: 60s` to the aegis-v2 service definition
SC-02: SubscriptionManager skeleton in subscription_manager.rs — use tokio::sync::RwLock for active_line_count (NOT Mutex). Use tokio::sync::Semaphore(100) for the ≤100 constraint. Do NOT hold any lock across .await network calls. ACK via AtomicUsize only. (v21-FIX-1)
SC-03: LineBudget struct {carry: usize, active: usize, scan: usize} with hard assert!(carry + active + scan <= 100)
SC-04: Two-tier data architecture: (a) ibkr_broker.rs token bucket 60 req/10min, max 6 concurrent, exponential backoff on Error 162; (b) ouroboros/data_fetch.py uses Polygon.io for nightly 5000+ tickers; (c) separate Python token bucket for Ouroboros
SC-05: MINIMUM_ENTRY_GBP: f64 = 1500.0 — pre-entry gate in risk_arbiter.rs. SUSPENDED when validated_trades_count < 250. Gate re-activates automatically at trade 250.
SC-06: Dust guard — FILLED portion < £500.0 → submit Peg-to-Mid limit order at (bid+ask)/2, TIF=3min; if not filled after 3min → submit market-sell; cancel unfilled remainder separately
SC-07: Fix V1 S3 contradiction — remove reactivation comment from mean_reversion.py
SC-08: APScheduler timezone audit in main.py — verify all pre-LSE jobs use timezone="Europe/London"
SC-09: crossbeam-channel bounded ring buffer (capacity=50000). On TrySendError::Full → DUAL PATH:
  (a) OFI path: emit QuoteImbalanceInvalidated { ticker_id, dropped_count } WAL event; suspend QI EWMA for that ticker until buffer clears (overflow_counter returns to 0 for ≥5 seconds). (v21-FIX-6)
  (b) Chandelier path: aggregate H/L/V into current OHLCV bar: bar.high = max(bar.high, tick.last); bar.low = min(bar.low, tick.last); bar.volume += tick.volume. (v20-FIX-13 retained)
SC-10: Internal cost-basis tracker: CostBasisEntry { total_cost_gbp: f64, total_shares: f64 }. avg_cost = total_cost / total_shares. Nightly clear at Ouroboros step 1 + IBKR reqPositions resync.
SC-11: SubscriptionManager active_line_count: AtomicUsize. Increment on reqMktData ACK, decrement on cancelMktData ACK. assert!(count <= 100) before every new subscription. DO NOT call reqOpenOrders for reconciliation — this is the wrong API and causes Error 3200 ban. AtomicUsize is sole truth. (v21-FIX-2)
SC-12: symbology_mapper.py — rules (a) space→dot, (b) LSE suffix→prefix, (c) exchange pass-through, (d) preferred shares BAC PR D → BAC/PD, (e) reverse mapping to_ibkr(polygon_symbol), (f) reverse split: adjust total_shares /= split_factor, price_basis *= split_factor
SC-13: (a) dynamic Kelly ramp: kelly_scale = max(0.1, min(1.0, validated_trades / 250.0)) in risk_arbiter.rs; (b) POLARS_MAX_THREADS=2 environment variable in docker-compose.yml under aegis-v2 service; (c) SplitAdjustment WalPayload variant
SC-14: reqMarketDataType(3) — add client.req_market_data_type(3) as THE FIRST CALL in ibkr_broker.rs::connect() before any subscribe_bars() or reqMktData calls
SC-15: StrategyId enum extension — add StrategyId::HotScanner and StrategyId::RotationScanner to types/enums.rs; verify WalPayload::PositionOpened and PositionClosed include strategy_id field
SC-16: shm_size: '2gb' — add to aegis-v2 service in docker-compose.yml. Verify inside container: `df -h /dev/shm` shows ≥2GB. (v21-FIX-5)
SC-17: WalPayload::QuoteImbalanceInvalidated variant — add to types/wal.rs with fields: ticker_id: TickerId, dropped_count: u32, resumed_at_ts: Option<u64>. Wire into channel.rs overflow path (SC-09a). (v21-FIX-6)
SC-18: tokio::signal ONLY for signal handling — remove ctrlc crate from Cargo.toml. Implement unified `select! { _ = signal::ctrl_c() => ..., _ = signal::unix(SignalKind::terminate()) => ... }` block in main.rs with WAL flush sequence before exit. (G2-IN12)
SC-19: NTP-safe token bucket — replace SystemTime subtraction with `current.saturating_duration_since(last)` in ibkr_broker.rs rate limiter. Add client_id rotation on Error 326: `client_id = 101 + (reconnect_attempt % 5)` in reconnect logic. (G2-IN10, G2-IN13)
SC-20: Cleanup bundle — (a) portfolio.rs: remove CostBasisEntry from HashMap on PositionClosed WAL event (G2-IN16); (b) symbology_mapper.py: add .R suffix filter → InstrumentType::Rights → excluded from subscription (G2-M27); (c) types/wal.rs: extend QuoteImbalanceInvalidated to also suppress VPIN scoring for 30s (G2-R22); (d) ouroboros/symbology_mapper.py: add Redis symbology cache with 24h TTL, disk fallback (G2-I31)

After all 20 items have passing tests:
- Run cargo test (all tests must pass)
- Run docker build (must succeed)
- Verify docker-compose.yml has ALL THREE: stop_grace_period: 60s, POLARS_MAX_THREADS=2, shm_size: '2gb'
- Run `docker exec aegis-v2 df -h /dev/shm` → shows ≥2GB
- Run a 30-minute paper session to verify SC-01 SIGTERM drill end-to-end
- Verify reqMarketDataType(3) appears as FIRST IBKR call in paper session logs
- Verify grep on subscription_manager.rs: NO reqOpenOrders calls
- Verify grep on Cargo.toml: NO ctrlc crate dependency (SC-18)
- Verify NTP saturating_duration_since in token bucket (SC-19)
- Verify .R suffix ticker rejected from subscription (SC-20)
- Verify symbology Redis cache populated after Ouroboros Step 1 (SC-20)

Do NOT start Phase 11 until Phase 8 gate is fully signed off.
```

---

*AEGIS_MASTER_PLAN_v21.md — Generated 2026-03-09 | Last updated: 2026-03-09 (full triage integration)*
*Supersedes: AEGIS_MASTER_PLAN_v20.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v20.md (Gemini Institutional Syndicate 200-bullet adversarial audit + Part 2 Red Team + Part 3 Top 10 Priority Fixes)*
*Full triage integration: ALL 92 accepted items from AEGIS_SELF_ANALYSIS_TRIAGE_v20.md are now in v21 (92 items: 5 P0 + 72 P1 + 15 IMPROVEMENTS, across Phases 8-22)*
*10 v21-FIX priority fixes in Part 1 delta table + all remaining accepted items added as "Additional Full Triage Amendments" to each phase*
*Deferred: 61 NOTED items (post-Crucible) + 30 ACADEMIC items (Q2+) documented in AEGIS_SELF_ANALYSIS_TRIAGE_v20.md*
*10 v21 fixes: G2-P1 through G2-P10 from Gemini priority matrix*
*Total acceptance tests: ~230 (vs 226 in v20, +~4 new tests covering v21 fixes)*
*Total remaining hours: 337h (vs 322h in v20, +15h for v21 additions)*
