# AEGIS V2 — MASTER PLAN v27
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 27.0 | **Date**: 2026-03-10 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v26. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v26.md — the Gemini G8 "Institutional Syndicate" 200-bullet adversarial audit of v26. New fixes are marked **[v27-FIX-N]** for traceability. The G8 audit found 11 genuine priority fixes (G8-P1 through G8-P11), 3 improvements (G8-I1 through G8-I3), and 5 operational fixes (G8-O1 through G8-O5). The remaining ~165 bullets were duplicates, academic deferrals, or FUD. G8 is the first audit where the majority of bullets targeted **interactions between v26 fixes** rather than standalone gaps — a natural maturation signal.

---

## v27 DELTA — G8 PRIORITY FIXES

| Fix | G8 ID | Trap | What was wrong in v26 | What v27 does |
|-----|-------|------|-----------------------|---------------|
| **v27-FIX-1** | G8-P1 | Watchdog `std::fs::write` blocks on hung EBS | v26-FIX-1 writes `emergency_state.json` to `/app/logs/` via `std::fs::write` before `_exit(1)`. If EBS I/O burst balance is exhausted (the most common AWS root cause of Tokio reactor deadlocks), the write blocks on the same frozen I/O path. Watchdog never reaches `_exit(1)`. System hangs forever with open positions unmanaged. | Primary write to `/dev/shm/aegis_emergency.json` (RAM-backed tmpfs, immune to EBS hang). Then non-blocking best-effort write to `/app/logs/` via `O_NONBLOCK` open (fails fast if EBS hung). Then `_exit(1)`. On boot: check `/dev/shm/` first, then `/app/logs/`. |
| **v27-FIX-2** | G8-P2 | Error 322 evict-then-retry creates Thompson Sampler oscillation loop | v26-FIX-9 evicts the lowest-priority subscription on Error 322, then immediately retries. Thompson Sampler may re-select the evicted ticker on the next cycle (it may have high posterior). Re-subscription triggers another Error 322. Subscribe/evict loop → rapid-fire IBKR message storm → Error 100 pacing ban → socket drop. | 5-minute `EvictionCooldown` cache per ticker. Thompson Sampler skips tickers in cooldown. Cooldown logged as `SubscriptionCoolingDown { ticker_id, remaining_secs }`. |
| **v27-FIX-3** | G8-P3 | EVT β→0 returns zero heat: approves max leverage into frozen assets | v26-FIX-5 returns `CvarHeat::zero()` when β<1e-8. Zero CVaR heat = no tail-risk veto. If spread is also narrow (halted asset with last known spread), Kelly allocator may approve full-size entry into an asset experiencing price discovery failure. | Replace `CvarHeat::zero()` with `CvarHeat::max_historical(ticker)`. On β→0: look up asset's maximum observed CVaR heat from trailing 30-day window in `asset_volatility.json`. Return that heat. If no historical data (new asset): return `DEFAULT_MAX_HEAT = 0.95` (near-certain veto). Fail-safe: unknown volatility = maximum observed risk. |
| **v27-FIX-4** | G8-P4 | Chandelier dividend fix modifies live price: distorts ATR True Range | v26-FIX-10 computes `adjusted_price = current_price + dividend_amount` and evaluates Chandelier stop against it. This inflates True Range on ex-date (H-PrevC becomes artificially large), which increases ATR, which perversely widens the Chandelier stop — the opposite of the intended protection. | Do NOT modify `current_price`. Instead, on ex-date, adjust `highest_high` downward: `self.highest_high = (self.highest_high - div).max(current_price)`. Leave `current_price` untouched. Evaluate stop against unmodified `current_price` as normal. This correctly re-anchors the Chandelier to post-dividend price structure without corrupting ATR. |
| **v27-FIX-5** | G8-P5 | Phantom position adoption bypasses ISA checks and lacks StrategyId | v26-FIX-6 detects phantom positions (in IBKR but not in WAL) and logs/alerts. But it then attempts to manage them. Phantoms: (a) may not be ISA-eligible, (b) have no StrategyId, (c) have no Kelly fraction, (d) have unknown highest_high for Chandelier. Managing them risks ISA violations and uncontrolled sizing. | Instead of managing phantom positions: immediately liquidate via TWAP with `StrategyId::ManualRecovery`. Send Telegram alert. Free slot. Engine operates from clean known state after liquidation. |
| **v27-FIX-6** | G8-P6 | Polygon /upcoming misses non-US ad-hoc closures | Polygon `/v1/marketstatus/upcoming` is SIP-feed based and primarily covers US exchanges. For Asian ad-hoc closures (HKEX Typhoon Signal 8, KRX ad-hoc) and European MTF emergency closures, Polygon may return "Open" when the exchange is actually closed. Settlement math breaks. | For non-US exchanges (HKEX, KRX, TSE, ASX): use cal-date + add 1 extra safety buffer day (not Polygon). At Rust order time: call `reqTradingHours(conid)` and verify today is actually a trading day for that specific exchange. If not → skip entry. |
| **v27-FIX-7** | G8-P7 | 15s contractDetailsEnd timeout: partial universe distorts Thompson Sampler | v26-FIX-3 processes the partial universe on 15s timeout. Thompson Sampler normalizes posterior probabilities across available arms only. A 20% universe means remaining arms get 5× inflated probability mass → capital routes to sub-optimal assets that only rank highly due to missing competition. | On timeout: merge partial with previous day's `universe_cache.json` — prefer fresh data for tickers in the partial, keep stale data for missing tickers. `universe_cache.json` written after every successful full Ouroboros run (atomic CRC32). |
| **v27-FIX-8** | G8-P8 | HALT acknowledgment invisible when send queue backed up during 429 backoff | v26-FIX-4 decoupled send_task and poll_task. If send_task is in 429 backoff (150s sleep), poll_task receives `/HALT` and queues the "System halting" ack behind the backoff window. Operator sees silence. Cannot confirm if HALT took effect. | `poll_task` writes HALT receipt to `/dev/shm/halt_ack.json` immediately on receipt (RAM-backed, instant, bypasses send queue). Local log `HALT COMMAND RECEIVED`. Telegram ack queued separately (delivered when backoff clears — operator has local confirmation in the meantime). |
| **v27-FIX-9** | G8-P9 | reqMarketDataType(3) not re-sent after data farm reset | v26-FIX-8 gates `reqMarketDataType(3)` on `nextValidId`, which fires exactly once on initial connection. If IBKR gateway internally resets its data farm (Error 2104→2106 cycle), `nextValidId` is not resent. `reqMarketDataType(3)` is never re-sent. Subsequent data requests fall back to live mode → Error 162 rejections → system operates blind. | Also send `reqMarketDataType(3)` on receipt of IBKR Error 2106 (data farm restored). Log `ReqMarketDataTypeSent { trigger: "2106" }` WAL event. |
| **v27-FIX-10** | G8-P10 | Polygon 429 backoff can breach 23:00 UTC Mode A deadline | v26-FIX-7 uses exponential backoff with jitter but no ceiling. After 5 retries with Retry-After=60: cumulative wait = 1920s (~32 minutes). Ouroboros starting at 23:00 ET (~04:00 UTC) breaches 07:00 UTC Mode A open. Asian session missed entirely. | Hard cap total cumulative backoff at 15 minutes (900s). On breach: abort the Ouroboros step → load yesterday's cached artifact for that step → log `OuroborosStepAbortedFallback { step, reason: PolygonTimeout }` → advance to next step. |
| **v27-FIX-11** | G8-P11 | positionEnd missing on empty portfolio: triggers false Orange on boot | v26 boot reconciliation waits up to 30s for `positionEnd` callback. IBKR does not guarantee `positionEnd` on empty account — it may never fire. 30s timeout triggers → boot incorrectly interprets as reconciliation failure → Orange tier on an empty portfolio. | Track whether any `position()` callbacks arrived during the 30s window. If `position_count == 0 && !position_end_received`: assume clean empty portfolio — log `CleanEmptyPortfolio`, do NOT trigger Orange. If positions received but no positionEnd: Yellow (likely truncated response). |

**v27-MINOR-FIXES** (operational):
- **`/dev/shm` free space check**: `statvfs` on `/dev/shm` before emergency write; if <1MB skip write and proceed directly to `_exit(1)` (G8-O1)
- **Polygon /upcoming empty array fallback**: if empty array AND within 7 days of major holiday → use cal-date exclusively (G8-O2)
- **Telegram send queue bounded (cap=500)**: drop-oldest policy + priority lane (HALT/ORANGE/RED never dropped) (G8-I1)
- **Telegram send queue Redis persistence**: flush to Redis on graceful shutdown; reload on boot (G8-O3)
- **nextValidId coordinator**: tokio oneshot → serializes post-connect init: (1) reqMarketDataType, (2) reqPositions, (3) reqTradingHours (G8-O4)
- **Special dividend filter reset**: `is_special_dividend` flag from Polygon `dividend_type:"SC"` → Kalman/CUSUM reset on next Ouroboros (G8-O5)
- **universe_cache.json**: written atomically (CRC32) after every successful full Ouroboros run (G8-I2)
- **Phantom position MFE init (optional)**: before liquidation, try `reqHistoricalTicks` for `highest_high` init; if ISA-eligible with confirmed StrategyId match → operator Telegram confirmation for recovery path (G8-I3)

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

*(unchanged from v26)*

| Component | Status | Critical Issues |
|-----------|--------|----------------|
| **S15 daily_target.py** | ACTIVE | 0% win rate on 52 paper trades — execution timing root cause |
| **S3 mean_reversion.py** | DORMANT | Hard ETP veto correct; V2.1 comment removed (SC-07) |
| **chandelier_exit.py** | ACTIVE | Le Beau 5-rung; Redis-persisted (7-day TTL) |
| **cross_asset_macro.py** | ACTIVE | VIX 5-min cache; weekly HMM refit |
| **ml_meta_model.py** | DISABLED | Circular feedback; fabricated data |
| **uk_isa/ (15 files)** | ACTIVE | 12 leveraged ETPs |
| **sprint6_live_gate.py** | NOT MET | 0% WR; need 63+ MTRL days |
| **state_manager.py** | ACTIVE | Redis SSOT V8.0 |
| **feeds/data_feeds.py** | ACTIVE | TwelveData daily guard FIXED (2026-03-10): max_calls_per_day=750 |

---

### 1.2 V2 Rust Engine — Complete Module Inventory

**Status: Phases 1-7 COMPLETE. ~9,000 LOC. 147+ tests.**

*(unchanged from v26)*

---

## PART 2 — COMBINED ADVERSARIAL AUDIT TRIAGE SUMMARY

### 2.1 Combined P0 + P1 Matrix (all versions)

**P0 — Fatal:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-1 | Docker SIGKILL at 10s | `stop_grace_period: 60s` | **v20-FIX-1, Phase 8** |
| P0-2 | Polars vCPU starvation | `POLARS_MAX_THREADS=2` | **v20-FIX-2, Phase 8** |
| P0-3 | Half-Kelly + Min Entry = 0 trades | Dynamic Kelly ramp floor 0.1× | **v20-FIX-3, Phase 8/15** |
| P0-4 | WAL compaction severs positions | active_state.wal nightly rewrite | **v20-FIX-4 + v21-FIX-9, Phase 22** |
| P0-5 | reqPnL 1-per-connection limit | Account-level reqPnL | **v20-FIX-5, Phase 20** |
| P0-6 | clock.rs BST missing % 86400 | chrono-tz Europe::London | **v20-FIX-6, Phase 11** |
| P0-7 | RwLock writer starvation | AtomicUsize(Relaxed) + Semaphore(100) | **v24-FIX-6, Phase 8** |
| P0-8 | reqMarketDataType(3) before nextValidId | Gated on next_valid_id() + re-sent on Error 2106 | **v20-FIX-8 + v26-FIX-8 + v27-FIX-9, Phase 8** |
| P0-9 | Heartbeat only in DARK | Engine-side 30-min Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent | HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |
| P0-11 | reqOpenOrders Error 3200 ban | Internal AtomicUsize only | **v21-FIX-2, Phase 11** |
| P0-12 | Docker /dev/shm 64MB → Bus error | shm_size: '2gb' + cgroup 3g limit | **v21-FIX-5 + v26-minor, Phase 8** |
| P0-13 | bypass-permissions LLM root access | accept-edits ONLY | **v22-FIX-6, Process** |
| P0-14 | Engine deadlock: no watchdog | std::thread watchdog | **v23-FIX-11, Phase 8** |
| P0-15 | Watchdog exit(1) corrupts WAL | libc::kill(SIGTERM) | **v24-FIX-1, Phase 8** |
| P0-16 | Watchdog SIGTERM ignored by PID 1 | libc::_exit(1) fallback after 5s | **v25-FIX-1, Phase 8** |
| P0-17 | _exit(1) leaves no position record | emergency_state.json + boot reconciliation | **v26-FIX-1, Phase 8/22** |
| P0-18 | Watchdog std::fs::write blocks on hung EBS | Primary write to /dev/shm (RAM-backed); O_NONBLOCK EBS attempt | **v27-FIX-1, Phase 8** |

**P1 — High:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | EOD spread cache + weekend stale | actual_trading_hours_since staleness guard | **v22-FIX-2 + v24-FIX-3 + G6-I1, Phase 12** |
| P1-2 | Telegram polling dies silently | keep-alive + retry + decoupled send/poll tasks | **Phase 17 + v26-FIX-4** |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker invalidation | **v20-FIX-12, Phase 15** |
| P1-4 | Beta-Bernoulli negative EV | Gaussian-Gaussian Thompson Sampler | **v20-FIX-11, Phase 13** |
| P1-5 | QI suspension at peak alpha | COF directional BidSize/AskSize delta | **v22-FIX-3 + v24-FIX-7 + v25-FIX-7, Phase 8** |
| P1-6 | σ_noise overnight gap bias | hybrid_intraday_atr_14_pct | **v22-FIX-10 + v24-FIX-8 + v25-FIX-5, Phase 13** |
| P1-7 | Corp action settlement lag | Business-day cal-date + market_status_cache | **v22-FIX-7 + v24-FIX-2 + v25-FIX-2 + v26-FIX-2, Phase 16** |
| P1-8 | WAL compaction unbounded | Nightly atomic rewrite | **v21-FIX-9 + v22-FIX-4, Phase 22** |
| P1-9 | reqPnL manual holdings crash | HashSet<conid> whitelist | **v21-FIX-10, Phase 20** |
| P1-10 | CF domain violation + EVT ξ uncapped | Maillard gate + GPD ξ-free; β→0 → max_historical heat | **v21-FIX-3 + v24-FIX-5 + v26-FIX-5 + v27-FIX-3, Phase 15** |
| P1-11 | Cost basis wrong after split | Nightly clear + reqPositions resync | Phase 8 |
| P1-12 | Dust slippage on illiquid | Peg-to-Mid TIF=3min | Phase 8 |
| P1-13 | AtomicUsize leaks on dropped ACK | Internal tracking only | **v21-FIX-2, Phase 11** |
| P1-14 | FTT intraday exemption | FTT per-ISIN market_cap threshold | Phase 18 + **v26-minor** |
| P1-15 | NZX misses opening auction | Pre-subscribe at 22:55 UTC | Phase 19 |
| P1-16 | ISA tax year Jan 1 not April 6 | April 6 boundary in isa_gate.rs | Phase 12 |
| P1-17 | HKEX board lot → 0-share | ETP fallback | Phase 12 |
| P1-18 | Polars OOM parallel steps | Sequential step enforcement | Phase 16 |
| P1-19 | Carry allocator assumes 3 not 6 | Dynamic: 100 − (carry_count × 2) | **v20-FIX-14, Phase 20** |
| P1-20 | Semaphore permit leak | SemaphorePermitGuard natural RAII + cancelMktData on Drop + priority drain | **v24-FIX-6 + v25-FIX-6 + v26-FIX-11, Phase 8/11** |
| P1-21 | active_state.wal non-atomic write | Prefix-header CRC32 + read_exact | **v22-FIX-4 + v24-FIX-9 + v25-FIX-3, Phase 22** |
| P1-22 | WAL replay timeout → Orange | Timeout → Yellow; size guard 100MB | **v24-FIX-4 + v25-FIX-10, Phase 22** |
| P1-23 | Thompson Sampler σ_0 gap bias | hybrid_intraday_atr_14_pct × 3.0 | **v24-FIX-8 + v25-FIX-5, Phase 13** |
| P1-24 | OFI from trade volume | Directional BidSize/AskSize COF | **v24-FIX-7 + v25-FIX-7, Phase 8** |
| P1-25 | T+2 hardcoded for NYSE | Per-exchange business-day settlement | **v24-FIX-2 + v25-FIX-2 + v26-FIX-2, Phase 16** |
| P1-26 | T+1/T+2 ignores bank holidays | cal-date + market_status_cache | **v25-FIX-2 + v26-FIX-2, Phase 16** |
| P1-27 | BufReader::read_line OOM | read_exact 9 bytes | **v25-FIX-3, Phase 22** |
| P1-28 | Watchdog SIGTERM ignored by PID 1 | _exit(1) + emergency_state.json | **v25-FIX-1 + v26-FIX-1, Phase 8** |
| P1-29 | aiohttp FD leak | try/finally explicit close | **v25-FIX-4, Phase 16** |
| P1-30 | COF directionality | prev_bid/ask tracking | **v25-FIX-7, Phase 8** |
| P1-31 | OwnedSemaphorePermit drops without cancelMktData | cancel_tx in Drop + priority drain | **v25-FIX-6 + v26-FIX-11, Phase 8** |
| P1-32 | contractDetailsEnd hangs forever | 15s timeout → merge with universe_cache | **v26-FIX-3 + v27-FIX-7, Phase 11** |
| P1-33 | Telegram 429 blocks HALT commands | Decoupled send_task / poll_task | **v26-FIX-4, Phase 17** |
| P1-34 | EVT β→0 NaN panic | β guard → max_historical CVaR heat (not zero) | **v26-FIX-5 + v27-FIX-3, Phase 15** |
| P1-35 | Skipped corrupt WAL → phantom position | Boot reqPositions reconciliation + TWAP liquidation | **v26-FIX-6 + v27-FIX-5, Phase 22** |
| P1-36 | Polygon 429 no jittered backoff | Exponential backoff + jitter + 15min cap | **v26-FIX-7 + v27-FIX-10, Phase 16** |
| P1-37 | reqMarketDataType(3) before nextValidId | Gated on next_valid_id() + re-sent on Error 2106 | **v26-FIX-8 + v27-FIX-9, Phase 8** |
| P1-38 | Error 322 not handled + oscillation loop | Evict + 5-min EvictionCooldown | **v26-FIX-9 + v27-FIX-2, Phase 11** |
| P1-39 | Chandelier triggers on dividend ex-date | Adjust highest_high downward (NOT adjusted_price up) | **v26-FIX-10 + v27-FIX-4, Phase 14** |
| P1-40 | T+1/T+2 misses unscheduled closures | Polygon market status cache | **v26-FIX-2, Phase 16** |
| P1-41 | Phantom position adoption bypasses ISA checks | ManualRecovery TWAP liquidation | **v27-FIX-5, Phase 22/14** |
| P1-42 | Polygon /upcoming misses non-US ad-hoc closures | +1 safety buffer + reqTradingHours at order time | **v27-FIX-6, Phase 8/16** |
| P1-43 | Partial universe distorts Thompson Sampler | Merge with universe_cache.json on timeout | **v27-FIX-7, Phase 11/16** |
| P1-44 | HALT ack invisible during 429 backoff | /dev/shm/halt_ack.json immediately on poll_task | **v27-FIX-8, Phase 17** |
| P1-45 | reqMarketDataType(3) not re-sent after farm reset | Also send on Error 2106 | **v27-FIX-9, Phase 8** |
| P1-46 | Polygon backoff breaches Mode A deadline | Cap at 15min cumulative; abort step → cache | **v27-FIX-10, Phase 16** |
| P1-47 | positionEnd missing on empty portfolio → false Orange | position_count==0 → CleanEmptyPortfolio (not Orange) | **v27-FIX-11, Phase 22** |

---

### 2.2 Binding Architectural Mandates (all versions + v27)

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Polars LazyFrame + Arrow zero-copy. `.optimize()` before `.collect()`. | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** crossbeam-channel (cap=50,000). Overflow → COF (directional BidSize/AskSize delta). | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox.** Token bucket 60/10min. Polygon 4 req/min dynamic bucket (confirmed Starter+). | Phase 8+16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed only when live position exists. | Phase 11 |
| **GEM-A5** | **Drawdown tiers.** Yellow / Orange / Red. WAL replay timeout → Yellow. WAL oversized → Yellow. Emergency boot → Yellow. | Phase 16 |
| **v20-A1** | **chrono-tz in clock.rs.** All London time via Europe::London. | Phase 11 |
| **v20-A2** | **Ordering::Relaxed for all AtomicUsize telemetry.** Semaphore(100) enforces budget. | Phase 8+ |
| **v20-A3** | **Gaussian-Gaussian Thompson Sampler.** σ_noise/σ_0 from hybrid_intraday_atr_14_pct. | Phase 13 |
| **v20-A4** | **Account-level reqPnL + CarryMonitor whitelist.** | Phase 20 |
| **v21-A1** | **No reqOpenOrders.** Internal AtomicUsize only. | Phase 11 |
| **v21-A2** | **shm_size: '2gb' + deploy.resources.limits.memory: 3g cgroup hard cap. (v26-minor)** | Phase 8 |
| **v21-A3** | **Maillard CF gate + EVT POT GPD.** ξ uncapped; ξ≥1 → CVaRExceeded; β→0 → max_historical CVaR heat (DEFAULT_MAX_HEAT=0.95). **(v27-FIX-3 corrects v26-FIX-5)** | Phase 15 |
| **v21-A4** | **COF aggregator.** Directional BidSize/AskSize delta. Zero-size → 0.5. | Phase 8 |
| **v21-A5** | **active_state.wal prefix-header.** read_exact 9 bytes. CRC before parse. | Phase 22 |
| **v22-A1** | **EXCHANGE_TIMEZONE_MAP + business-day settlement.** cal-date + market_status_cache for unscheduled closures. **(v26-FIX-2)** | Phase 16 |
| **v22-A2** | **intraday_spread_cache.json + actual_trading_hours_since staleness guard.** | Phase 12/16 |
| **v22-A3** | **accept-edits ONLY.** No bypass-permissions. | Process |
| **v23-A1** | **std::thread watchdog.** UTC arithmetic. SIGTERM → 5s grace → _exit(1). emergency_state.json before SIGTERM. **(v26-FIX-1)** | Phase 8 |
| **v23-A2** | **WAL replay timeout → Yellow. WAL oversized → Yellow.** | Phase 22 |
| **v24-A1** | **Hybrid intraday ATR.** max(H-L, gap_magnitude × 0.6). | Phase 13/16 |
| **v24-A2** | **COF not OFI during overflow.** Honest labeling. | Phase 8 |
| **v25-A1** | **SemaphorePermitGuard sends cancelMktData on Drop.** Priority drain in IBKR actor. **(v26-FIX-11)** | Phase 8/11 |
| **v25-A2** | **Ouroboros pre-flight RAM guard.** psutil + cgroup read. **(v26-minor G7-I1)** | Phase 16 |
| **v25-A3** | **Business-day settlement + Polygon market status cache.** cal-date fallback. **(v26-FIX-2)** | Phase 16 |
| **v25-A4** | **Schema version in all calibration JSON.** | Phase 16 |
| **v25-A5** | **Watchdog clock-independent.** UTC arithmetic only, never clock.rs. | Phase 8 |
| **v26-A1** | **Emergency boot recovery.** emergency_state.json written before SIGTERM. Boot detects absence of WAL SystemShutdown → Yellow + reqPositions reconciliation gate. **(v26-FIX-1 + v26-FIX-6)** | Phase 8/22 |
| **v26-A2** | **Polygon market status cache.** `/v1/marketstatus/upcoming` nightly → 30-day trading day calendar. Settlement lag and ex-date blocklist use cache as ground truth. **(v26-FIX-2)** | Phase 16 |
| **v26-A3** | **reqMarketDataType(3) gated on nextValidId.** Never sent in connect(). **(v26-FIX-8)** | Phase 8 |
| **v26-A4** | **Error 322 eviction-before-retry.** Distinct from Error 3200 pacing. **(v26-FIX-9)** | Phase 11 |
| **v26-A5** | **Chandelier ex-date dividend adjustment.** Adjust `highest_high` downward by dividend amount on ex-date. Leave current_price untouched. **(v27-FIX-4 corrects v26-FIX-10's adjusted_price approach)** | Phase 14 |
| **v27-A1** | **Watchdog emergency write to /dev/shm.** Primary: `/dev/shm/aegis_emergency.json` (RAM, always writable). Secondary: O_NONBLOCK attempt to `/app/logs/`. Then `_exit(1)`. Boot: check /dev/shm first. **(v27-FIX-1)** | Phase 8 |
| **v27-A2** | **EvictionCooldown 5-min per-ticker.** Error 322 eviction enters 5-min cooldown. Thompson Sampler skips tickers in cooldown. Prevents subscribe/evict oscillation storm. **(v27-FIX-2)** | Phase 11 |
| **v27-A3** | **EVT β→0 → max_historical CVaR heat.** DEFAULT_MAX_HEAT=0.95 if no history. Never zero on β→0. **(v27-FIX-3)** | Phase 15 |
| **v27-A4** | **Phantom positions → ManualRecovery TWAP liquidation.** Default path: immediate TWAP liquidation via StrategyId::ManualRecovery. No management of unknown positions. **(v27-FIX-5)** | Phase 22/14 |
| **v27-A5** | **universe_cache.json written after every successful Ouroboros run.** Merged with partial on contractDetailsEnd timeout. Ensures Thompson Sampler always operates on full expected universe. **(v27-FIX-7)** | Phase 11/16 |

---

### 2.3 Recurring FUD Patterns (Documented for G9+ Audit Resistance)

| Pattern | Correct Response |
|---------|-----------------|
| "Float precision causes CRC32 mismatch" | CRC32 is hash-of-bytes; writer and reader see identical bytes on disk. Not a CRC32 issue. |
| "VaR is not sub-additive" | AEGIS uses CVaR (Expected Shortfall) for sizing — coherent and sub-additive (Artzner et al. 1999). VaR is display-only. |
| "OFI net delta = no sequence to process" | COF processes individual IBKR tick callbacks. The 100ms is the overflow accumulation window, not the delivery granularity. |
| "MAX_CARRY_POSITIONS limits capital efficiency" | By design. Tunable post-Crucible based on validated Sharpe. |
| "3x ETPs are unsuitable for automated trading" | False. 12 large-cap LSE leveraged ETPs with multi-million £ daily volume. By design. |
| "Polygon /upcoming covers all exchanges" | PARTIALLY valid concern addressed by v27-FIX-6: non-US exchanges use +1 safety buffer + reqTradingHours cross-reference at order time. |

**Expected G9 Pattern**: Protocol-level IBKR API state machine bugs, Tokio executor starvation under specific scheduling patterns, Polars Arrow memory layout assumptions on ARM vs x86. First audit where purely protocol-level edge cases dominate.

---

### 2.4 Deferred (Post-Crucible)

*(v26 defer table + v27 additions)*

| Finding | Reason |
|---------|--------|
| All prior deferred items | Unchanged from v26 |
| mmap lock-free emergency state | SUPERSEDED by v27-FIX-1 simpler /dev/shm approach |
| β→0 Balkema-de Haan point mass axiom | Addressed pragmatically by v27-FIX-3 max_historical heat |
| Thompson Sampling partial universe regret bounds | Addressed pragmatically by v27-FIX-7 cache merge |
| Hill estimator dynamic EVT threshold | Post-Crucible calibration data needed |
| Volume Profile TWAP slicing | Phase Q2+ execution |
| VIX term structure carry cap | Phase Q2+ macro overlay |
| Full L2 order book for true OFI | IBKR L2 subscription needed |
| Neural Hawkes / DQN / DPDK / Rust FFI | Phase Q3-Q4 Quantum Apex |

---

## PART 3 — PHASE PLAN

### Numbering Convention
- **Phases 1-7**: COMPLETE
- **Phase 8**: Next — **20 SC items** (updated for v27)
- **Phases 11-23**: Granular build

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 61.7h | **Status**: NEXT
*(+3.2h vs v26: v27-FIX-1 /dev/shm watchdog +1h, v27-FIX-9 Error 2106 reqMarketDataType +0.5h, v27-FIX-6 reqTradingHours pre-trade check +1h, v27-A5 nextValidId coordinator +0.5h, v27-O1 statvfs +0.2h)*

**v27 Amendments:**

- **Watchdog /dev/shm primary write (v27-FIX-1):** In SC-18-W watchdog, replace the `std::fs::write("/app/logs/emergency_state.json", ...)` call with:
  ```rust
  // In watchdog, before libc::kill(SIGTERM):
  let payload = format!("{{\"ts\":{},\"pid\":{}}}", now, unsafe { libc::getpid() });
  // Primary: /dev/shm (RAM-backed, immune to EBS freeze)
  // Check free space first (statvfs)
  let shm_ok = unsafe {
      let mut stat = std::mem::zeroed::<libc::statvfs>();
      libc::statvfs(b"/dev/shm\0".as_ptr() as *const libc::c_char, &mut stat) == 0
          && (stat.f_bfree * stat.f_frsize) > 1_048_576  // >1MB free
  };
  if shm_ok {
      let _ = std::fs::write("/dev/shm/aegis_emergency.json", &payload);
  }
  // Best-effort EBS write (O_NONBLOCK — fails fast if EBS hung)
  let _ = std::fs::OpenOptions::new()
      .write(true).create(true)
      .custom_flags(libc::O_NONBLOCK)
      .open("/app/logs/emergency_state.json")
      .and_then(|mut f| { use std::io::Write; f.write_all(payload.as_bytes()) });
  // Now kill and exit
  unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
  std::thread::sleep(Duration::from_secs(5));
  unsafe { libc::_exit(1) };
  ```

- **reqMarketDataType(3) on Error 2106 (v27-FIX-9):** In IBKR error handler:
  ```rust
  2106 => {
      log::info!("Data farm restored. Re-sending reqMarketDataType(3).");
      self.client.req_market_data_type(3);
      self.wal.write(WalPayload::ReqMarketDataTypeSent { trigger: "2106" });
  }
  ```

- **nextValidId coordinator (G8-O4):** `next_valid_id()` fires a tokio oneshot that triggers a coordinator task. Coordinator serializes post-connect init:
  1. `reqMarketDataType(3)` — write WAL event
  2. `reqPositions` (if emergency boot)
  3. Schedule `reqTradingHours` cache refresh
  Each step completes before next begins. No concurrent IBKR API state machine violations.

- **Pre-trade reqTradingHours for non-US (v27-FIX-6):** In `ibkr_broker.rs`, before placing any non-US order: call `reqTradingHours(conid)` and verify today is a trading day for that exchange. If not → skip entry, log `TradingHoursVeto { ticker_id, exchange }`.

- **Boot emergency state check updated (v27-FIX-1):** In main.rs, boot sequence checks `/dev/shm/aegis_emergency.json` first, then `/app/logs/emergency_state.json`:
  ```rust
  let emergency = std::path::Path::new("/dev/shm/aegis_emergency.json").exists()
      || std::path::Path::new("/app/logs/emergency_state.json").exists();
  ```

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-01** | SIGTERM handler: `tokio::signal::ctrl_c()` + `tokio::signal::unix::signal(SignalKind::terminate())`. Flatten → 30s wait → WAL SystemShutdown → exit. | main.rs | v23-FIX-8 |
| **SC-01a** | `stop_grace_period: 60s` + `restart: unless-stopped` + `deploy.resources.limits.memory: 3g` in docker-compose.yml | docker-compose.yml | v20-FIX-1 + v26-minor |
| **SC-02** | SubscriptionManager. Ordering::Relaxed. `SemaphorePermitGuard { _permit, ticker_id, cancel_tx }`. Drop: try_send cancel_tx + _permit auto-drops. No mem::forget. No add_permits. | subscription_manager.rs | v24-FIX-6 + v25-FIX-6 |
| SC-03 | LineBudget `{carry, active, scan}` with assert ≤ 100 | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket 60/10min; Polygon 4 req/min dynamic bucket (Starter+ confirmed, unlimited daily) | ibkr_broker.rs + data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP = 1500.0` — suspended while validated_trades < 250 | risk_arbiter.rs | v20-FIX-3 |
| SC-06 | Dust guard < £500 → Peg-to-Mid TIF=3min → market-sell | exit_engine.rs | v19-FIX-1 |
| SC-07 | Remove V1 S3 reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler audit — all pre-LSE jobs timezone="Europe/London" | main.py | — |
| **SC-09** | crossbeam-channel (cap=50000). COF overflow path: directional prev_bid/ask tracking. Chandelier path. AT-60, AT-60b, AT-60c. | python_bridge.rs + channel.rs + types/wal.rs | v24-FIX-7 + v25-FIX-7 |
| SC-10 | CostBasisEntry HashMap; nightly clear + reqPositions resync | portfolio.rs | G-09 |
| SC-11 | AtomicUsize Relaxed; no reqOpenOrders | subscription_manager.rs | v21-FIX-2 |
| SC-12 | symbology_mapper.py — all 6 rules | ouroboros/symbology_mapper.py | v19-FIX-2 |
| SC-13 | kelly_scale ramp + POLARS_MAX_THREADS=2 + SplitAdjustment WAL | risk_arbiter.rs + docker-compose.yml | v20-FIX-3 |
| **SC-14** | `reqMarketDataType(3)` gated on `next_valid_id()` callback **(v26-FIX-8)**. NOT called in connect(). Also re-sent on Error 2106 **(v27-FIX-9)**. `ReqMarketDataTypeSent` WAL event (trigger field: "nextValidId" or "2106"). AT-14b: delayed nextValidId → reqMarketDataType sent only after callback. AT-14c: inject Error 2106 → reqMarketDataType re-sent. | ibkr_broker.rs | v20-FIX-8 + v26-FIX-8 + v27-FIX-9 |
| SC-15 | StrategyId::HotScanner + StrategyId::RotationScanner + StrategyId::ManualRecovery | types/enums.rs | v20-FIX-10 + v27-FIX-5 |
| SC-16 | shm_size: '2gb' in docker-compose.yml | docker-compose.yml | v21-FIX-5 |
| SC-17 | WalPayload::QuoteImbalanceCompressed { ticker_id, bid_size_delta_sum, ask_size_delta_sum, dropped_count } | types/wal.rs | v24-FIX-7 |
| **SC-18-W** | **Watchdog (v27-FIX-1 + v26-FIX-1 + v25-FIX-1):** UTC arithmetic market window. Deadlock detected: (1) statvfs /dev/shm, if >1MB free → write `/dev/shm/aegis_emergency.json`; (2) O_NONBLOCK attempt to `/app/logs/emergency_state.json`; (3) libc::kill(SIGTERM); (4) sleep(5s); (5) libc::_exit(1). #[serial_test::serial] on all LAST_TICK_TS tests. AT-18e: _exit fires ≤70s. AT-18g: emergency_state.json (either path) on boot → Yellow + reconciliation. AT-18h: mock std::fs::write EBS with infinite sleep → verify /dev/shm write succeeds → verify _exit(1) reached. | watchdog.rs + main.rs | v24-FIX-1 + v25-FIX-1 + v26-FIX-1 + v27-FIX-1 |
| SC-19 | contractDetailsEnd handler (base; Phase 11 adds 15s timeout + universe_cache merge) | subscription_manager.rs | v24-minor |
| **SC-20** | nextValidId coordinator — tokio oneshot → serialized post-connect sequence: reqMarketDataType(3) → reqPositions (if emergency boot) → reqTradingHours cache refresh. Pre-trade reqTradingHours check for non-US assets. | ibkr_broker.rs + main.rs | G8-O4 + v27-FIX-6 |

**Gate**: All 20 SC items pass. `cargo test` output pasted. Greps: no AcqRel/SeqCst, no mem::forget, no add_permits, no process::exit, UTC arithmetic in watchdog, _exit(1) present, reqMarketDataType NOT in connect() BUT IS in next_valid_id() AND in Error 2106 handler. AT-14b, AT-14c, AT-18e, AT-18f, AT-18g, AT-18h, AT-60c all pass. Emergency state written to /dev/shm after simulated EBS hang. Boot reconciliation gate verified.

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 28h | **Depends on**: Phase 8
*(+2.5h vs v26: EvictionCooldown 5-min cache +1.5h, universe_cache merge on timeout +1h)*

**v27 Amendments:**

- **EvictionCooldown (v27-FIX-2):** Add `EvictionCooldown` struct to `subscription_manager.rs`:
  ```rust
  struct EvictionCooldown {
      evicted_at: HashMap<TickerId, Instant>,
      cooldown: Duration,  // 5 minutes
  }

  impl EvictionCooldown {
      fn can_subscribe(&self, ticker_id: TickerId) -> bool {
          self.evicted_at.get(&ticker_id)
              .map(|t| t.elapsed() > self.cooldown)
              .unwrap_or(true)
      }
      fn record_eviction(&mut self, ticker_id: TickerId) {
          self.evicted_at.insert(ticker_id, Instant::now());
      }
  }
  ```
  Thompson Sampler skips tickers where `!can_subscribe(ticker_id)`. Cooldown logged as `SubscriptionCoolingDown { ticker_id, remaining_secs }`.

- **contractDetailsEnd 15s timeout + universe_cache merge (v26-FIX-3 + v27-FIX-7):** On 15s timeout (v26): do NOT use the partial alone. Merge with `universe_cache.json`:
  ```python
  # After contractDetailsEnd timeout:
  if timeout_hit:
      logger.warning("contractDetailsEnd timeout. Merging partial with cache.")
      prev_universe = load_json('universe_cache.json')
      merged = {**prev_universe, **partial_received}  # partial overrides stale
      write_json('universe_cache.json', merged)
      universe = merged
  else:
      write_json('universe_cache.json', full_received)  # atomic CRC32
      universe = full_received
  ```

- **Error 322 handler (v26-FIX-9):** Unchanged from v26 — evict lowest-priority scan subscription, retry original. Now combined with EvictionCooldown (v27-FIX-2) to prevent re-subscription storm.

- **reqContractDetails pagination (G7-O1):** Unchanged from v26 — 500 tickers/batch.

- **CancelMktData priority drain (v26-FIX-11):** Unchanged from v26.

**Deliverables:**
- `clock.rs` REWRITTEN — chrono-tz; TradingMode enum
- `subscription_manager.rs` — all v26 + EvictionCooldown (v27-FIX-2) + universe_cache merge on timeout (v27-FIX-7)
- `cancel_mktdata_actor.rs` — priority drain loop
- `mode_controller.rs` — capacity=64

**Acceptance Tests:**
- AT-19b: contractDetailsEnd dropped at 3000/5000 tickers → 15s timeout → merge with cache → 5000-ticker universe used (not 3000)
- AT-19c (NEW): contractDetailsEnd timeout at 3000/5000 → verify cache merge → Thompson Sampler denominator = 5000
- AT-20b: Error 322 on new subscription → lowest-priority scan evicted → original subscription retried → active_line_count ≤ 100
- AT-20c (NEW): Error 322 → eviction → same ticker NOT re-subscribed for 5 minutes → no Error 100 during cooldown window

**Gate**: 27 tests pass; EvictionCooldown prevents re-subscription storm; universe_cache merge on timeout verified; AT-19c and AT-20c pass; contractDetailsEnd timeout verified; Error 322 eviction verified

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 22.5h | **Depends on**: Phase 11
*(unchanged from v26)*

**Gate**: 26 tests pass; actual_trading_hours_since verified; Christmas Eve AT-37e passes

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 26.5h | **Depends on**: Phase 12
*(unchanged from v26)*

**Gate**: 24 tests pass; hybrid ATR used for all TS noise params; directional COF verified

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 26h | **Depends on**: Phase 13
*(+2h vs v26: corrected Chandelier highest_high adjustment +1h, ManualRecovery TWAP path +1h)*

**v27 Amendments:**

- **Chandelier dividend adjustment CORRECTED (v27-FIX-4):** Replace v26-FIX-10's `adjusted_price = current_price + dividend_amount` approach. Do NOT modify `current_price`. Instead, on ex-date:
  ```rust
  // On ex-date detection:
  if is_ex_date(ticker, today, &corp_action_blocklist) {
      let div = get_dividend_amount(ticker, today, &corp_action_blocklist);
      // Adjust historical peak downward — post-dividend, old high is no longer valid
      self.highest_high = (self.highest_high - div).max(current_price);
      log::info!("Chandelier highest_high adjusted -{} for dividend on {}", div, ticker);
  }
  // Then evaluate stop against unmodified current_price as normal
  let stop = self.highest_high - (atr * self.multiplier);
  if current_price < stop { /* exit */ }
  ```
  This correctly re-anchors the Chandelier without inflating ATR True Range.

- **ManualRecovery TWAP path (v27-FIX-5):** In `executioner_v2.rs`, add `StrategyId::ManualRecovery` TWAP path:
  ```rust
  pub async fn liquidate_twap(&self, position: PhantomPosition, strategy_id: StrategyId) {
      // TWAP liquidation: 5 equal slices over 15 minutes
      let slice_qty = position.qty / 5;
      for i in 0..5 {
          self.place_market_sell(position.ticker_id, slice_qty, strategy_id).await;
          if i < 4 { tokio::time::sleep(Duration::from_secs(180)).await; }
      }
  }
  ```

**Acceptance Tests:**
- AT-88b: REPLACED by AT-88c. QQQ3.L ex-date with 1% dividend → 1% price drop injected → `highest_high` reduced by 1% (NOT current_price increased) → ATR unaffected on ex-date bar → Chandelier stop evaluates correctly → NOT triggered on legitimate ex-date drop
- AT-76 through AT-78 (TWAP): unchanged
- AT-235c (partial — ManualRecovery TWAP): TWAP liquidation path verified here; boot reconciliation trigger tested in Phase 22

**Gate**: 20 tests pass; AT-88c verifies highest_high adjustment (not adjusted_price); ATR confirmed unaffected; ManualRecovery TWAP path verified; TWAP token bucket wiring verified

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 23h | **Depends on**: Phase 14
*(+0.5h vs v26: β→0 max_historical heat lookup + asset_volatility.json schema)*

**v27 Amendment:**

- **EVT β→0 CORRECTED (v27-FIX-3):** Replace v26-FIX-5's `CvarHeat::zero()` return. In `cvar_heat.rs`:
  ```rust
  if beta.abs() < 1e-8 {
      let max_heat = self.asset_volatility
          .get(ticker)
          .map(|v| v.max_cvar_heat_30d)
          .unwrap_or(DEFAULT_MAX_HEAT);  // DEFAULT_MAX_HEAT = 0.95
      log::warn!("EvtBetaNearZero {{ beta: {:.2e}, ticker: {} }} → max_historical_heat: {:.3}",
                 beta, ticker, max_heat);
      return Ok(CvarHeat::from(max_heat));
  }
  ```
  `asset_volatility.json` schema: add `max_cvar_heat_30d: f64` field alongside existing fields. Populated by Ouroboros step 8.

**Acceptance Tests:**
- AT-93g: REPLACED by AT-93h. β=1e-10 injected → max_historical CVaR heat returned (not zero) → RiskGate vetoes new entry → no panic; verify `max_cvar_heat_30d` field read from `asset_volatility.json`
- AT-93d: ξ≥1.0 CVaRExceeded → unchanged
- AT-93f: DCC-GARCH timeout recovery → unchanged

**Gate**: 27 tests pass; AT-93h passes (β→0 → max_historical heat, NOT zero); ξ≥1.0 CVaRExceeded (AT-93d); DCC-GARCH timeout recovery (AT-93f); ≥50 exceedances verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 42h | **Depends on**: Phase 15
*(+3.5h vs v26: non-US settlement +1 safety buffer +0.5h, special dividend flag +0.5h, universe_cache.json CRC32 write +0.3h, Polygon backoff cap +0.5h, empty-array fallback +0.3h, new ATs +1.4h)*

**v27 Amendments:**

- **Non-US settlement +1 safety buffer (v27-FIX-6):** In Ouroboros step 2:
  ```python
  def get_settlement_veto_date(ticker, ex_date, exchange, lag_days, market_status_cache):
      if exchange in ('NYSE', 'NASDAQ'):
          return subtract_trading_days(ex_date, lag_days, market_status_cache)
      else:
          # +1 extra safety buffer for non-US (Polygon coverage unreliable)
          return cal_subtract_business_days(ex_date, lag_days + 1, exchange)
  ```

- **Polygon backoff cap at 15 minutes (v27-FIX-10):** In `data_fetch.py`:
  ```python
  MAX_POLYGON_BACKOFF_SECS = 900  # 15 minutes total — prevents Mode A breach

  async def polygon_get_with_backoff(session, url, params, max_retries=5):
      cumulative_sleep = 0
      for attempt in range(max_retries):
          resp = await session.get(url, params=params)
          if resp.status == 429:
              retry_after = int(resp.headers.get('Retry-After', 60))
              jitter = random.uniform(0, retry_after * 0.2)
              sleep_secs = min(retry_after + jitter,
                               MAX_POLYGON_BACKOFF_SECS - cumulative_sleep)
              if sleep_secs <= 0:
                  logger.error("Polygon backoff budget exhausted. Loading cached artifact.")
                  return None  # Caller uses yesterday's artifact
              await asyncio.sleep(sleep_secs)
              cumulative_sleep += sleep_secs
              continue
          return resp
      return None
  ```
  On `None` return: load previous day's artifact for that step → `OuroborosStepAbortedFallback { step, reason: PolygonTimeout }` → advance to next step.

- **Polygon /upcoming empty array fallback (G8-O2):** If `/v1/marketstatus/upcoming` returns 200 OK but empty array AND current date is within 7 days of a known major holiday (from cal-date) → treat as Polygon data failure → use cal-date exclusively for that run.

- **Special dividend flag (G8-O5):** `corp_action_blocklist.json` schema: add `is_special_dividend: bool` from Polygon `dividend_type` field (`"SC"` = special cash). In Ouroboros step 2: if `is_special_dividend = true` → set flag on ticker for full Kalman/CUSUM filter reset on next HotScanner initialization.

- **universe_cache.json CRC32 write (v27-FIX-7 + G8-I2):** After every successful full Ouroboros run: write `universe_cache.json` atomically with CRC32 prefix-header (same format as active_state.wal). Validates cache integrity on read.

**Acceptance Tests:**
- AT-111g: Polygon market_status_cache unscheduled closure → settlement skips day → verified
- AT-111h (NEW): HKEX asset with ex_date during Typhoon closure (mock reqTradingHours returning closed) → trade blocked despite Polygon returning "Open"
- AT-120b: Polygon 429 with backoff → jittered backoff applied → pipeline continues
- AT-120c (NEW): Polygon returns 429 for all 5 retries → verify cumulative sleep ≤ 900s → verify fallback to previous artifact → verify Ouroboros advances to next step
- AT-121: Italian FTT per-ISIN → verified
- All prior v26 ATs retained

**Gate**: 46 tests pass; universe_cache.json written after successful run; cache merge on timeout verified; Polygon backoff cap ≤15min verified (AT-120c); non-US safety buffer verified; AT-111h passes; all prior gates pass

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 18.5h | **Depends on**: Phase 16
*(+1.5h vs v26: bounded send queue +0.5h, halt_ack.json +0.5h, Redis persistence +0.5h)*

**v27 Amendments:**

- **Bounded send queue with priority lane (G8-I1):** `telegram_reporter.py`:
  ```python
  send_queue = asyncio.Queue(maxsize=500)  # bounded — drop-oldest on overflow
  priority_queue = asyncio.Queue()  # unbounded — HALT/ORANGE/RED never dropped

  async def enqueue_message(msg, priority=False):
      if priority:
          await priority_queue.put(msg)
      else:
          if send_queue.full():
              try: send_queue.get_nowait()  # drop oldest
              except asyncio.QueueEmpty: pass
          await send_queue.put(msg)
  ```
  `send_task` drains priority_queue first, then send_queue.

- **HALT acknowledgment to /dev/shm (v27-FIX-8):**
  ```python
  async def handle_command(update):
      if update.text == '/HALT':
          logger.critical("HALT COMMAND RECEIVED AND ACKNOWLEDGED")
          with open('/dev/shm/halt_ack.json', 'w') as f:
              json.dump({'ts': time.time(), 'status': 'HALTING'}, f)
          await halt_channel.put(HaltCommand())
          await enqueue_message("AEGIS HALTING — command received.", priority=True)
  ```

- **Send queue Redis persistence (G8-O3):** On graceful shutdown (WAL SystemShutdown received): drain pending `send_queue` items to Redis list with 1h TTL. On boot: `LRANGE` pending items → reload into `send_queue` before starting `send_task`.

**Acceptance Tests:**
- AT-132b: HTTP 429 active with 150s retry_after → HALT command → poll_task receives within 5s → engine halts → verified (unchanged from v26)
- AT-132c (NEW): 429 backoff active (150s) → send /HALT → verify `halt_ack.json` written within 2s → verify engine enters halt state → verify Telegram ack delivered after backoff clears
- AT-133 (NEW): send_queue at capacity (500 msgs) → new non-priority alert → oldest dropped → new alert delivered; priority HALT → NOT dropped regardless of queue state

**Gate**: 22 tests pass; HALT ack in /dev/shm within 2s (AT-132c); priority lane verified (AT-133); bounded queue drop-oldest verified; Redis persistence on shutdown/reload verified; PDF cleanup verified; all prior Phase 17 gates pass

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 22h | **Depends on**: Phase 17
*(unchanged from v26)*

**Gate**: 28 tests pass; XETRA T-8 cutoff verified; Italian FTT per-ISIN verified; 5 paper trading days

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21.3h | **Depends on**: Phase 18
*(unchanged from v26)*

**Gate**: 21 tests pass; JPY truncation verified

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 24h | **Depends on**: Phase 19
*(unchanged from v26)*

**Gate**: 25 tests pass

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13.2h | **Depends on**: Phase 20
*(unchanged from v26)*

**Gate**: 17 tests pass; 96h freshness verified; 5 paper trading days

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 45.2h | **Depends on**: Phase 21
*(+2.2h vs v26: ManualRecovery TWAP liquidation +0.5h, positionEnd empty portfolio fix +0.5h, /dev/shm boot check +0.2h, new ATs +1h)*

**v27 Amendments:**

- **Phantom position → ManualRecovery TWAP liquidation (v27-FIX-5):** Replace v26's "log + Telegram alert" approach for phantom positions:
  ```rust
  for phantom in phantom_positions {
      if !isa_gate.is_eligible(&phantom.isin) {
          log::error!("PhantomPosition {} NOT ISA-eligible. Liquidating.", phantom.ticker);
      } else {
          log::warn!("PhantomPosition {} ISA-eligible but no WAL history. Liquidating.", phantom.ticker);
      }
      executioner.liquidate_twap(phantom, StrategyId::ManualRecovery).await;
      telegram.send(format!(
          "PHANTOM POSITION LIQUIDATED: {} — no WAL history. Manual review needed.",
          phantom.ticker
      )).await;
  }
  ```
  Slot freed after fill. Engine operates from clean known state.

- **Emergency boot path updated (v27-FIX-1):** Check `/dev/shm/aegis_emergency.json` first:
  ```rust
  let emergency = std::path::Path::new("/dev/shm/aegis_emergency.json").exists()
      || std::path::Path::new("/app/logs/emergency_state.json").exists();
  if emergency {
      log::error!("WatchdogEmergencyBoot detected. Forcing Yellow tier.");
      telegram.send("AEGIS EMERGENCY BOOT — reconciling positions.").await;
      self.drawdown_tier = DrawdownTier::Yellow;
      // ... reconciliation
      // Delete both paths after successful reconciliation
      let _ = std::fs::remove_file("/dev/shm/aegis_emergency.json");
      let _ = std::fs::remove_file("/app/logs/emergency_state.json");
  }
  ```

- **positionEnd missing on empty portfolio (v27-FIX-11):**
  ```rust
  if position_count == 0 && !position_end_received {
      log::info!("No positions in 30s. Assuming clean empty portfolio (normal on empty account).");
      // Do NOT trigger Orange — this is expected IBKR behavior on empty account
  } else if position_count > 0 && !position_end_received {
      log::warn!("Positions received but no positionEnd. Partial data. Yellow tier.");
      self.drawdown_tier = DrawdownTier::Yellow;
  }
  ```

**Acceptance Tests:**
- AT-235b: corrupted PositionClosed event skipped → phantom position detected → TWAP liquidation initiated → `PhantomPositionReconciled` logged → slot freed (updated from v26: liquidation verified, not just log)
- AT-235c (NEW): Boot with phantom ISA-eligible position → ManualRecovery TWAP liquidation → Telegram alert → slot freed after fill
- AT-241: emergency_state.json (either path) on boot → Yellow → Telegram → reqPositions → both files deleted after reconciliation (updated for /dev/shm)
- AT-241b (NEW): Boot with zero IBKR positions; positionEnd never fires; verify engine proceeds normally (not Orange); verify `clean_empty_portfolio` logged
- AT-242: WalEventCorrupt → quarantine.log verified (unchanged)

**Gate**: 39 tests pass; phantom positions → TWAP liquidation (AT-235c); positionEnd empty → not Orange (AT-241b); emergency boot checks both /dev/shm and /app/logs; quarantine log verified; all v26 gates retained; 48h continuous paper run

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(Suite 7 updated for v27)*

**Suite 7 updated for v27:**
- Emergency boot: simulate EBS hung + watchdog trip → verify `/dev/shm/aegis_emergency.json` written → verify boot detection (both paths) → Yellow + reconciliation
- Error 322 + EvictionCooldown: inject capacity exceeded → eviction + 5-min cooldown → no oscillation storm
- Chandelier corrected: ex-date dividend → `highest_high` adjusted downward (not adjusted_price up) → ATR unaffected
- Polygon market status cache + backoff cap: unscheduled closure + sustained 429 → both handled correctly
- Telegram: HALT command → `/dev/shm/halt_ack.json` written within 2s → engine halts → ack delivered when backoff clears
- EVT β→0: β=1e-10 → max_historical CVaR heat returned (not zero) → RiskGate vetoes → no panic
- Phantom position: corrupt WAL PositionClosed → boot reconciliation → TWAP liquidation → slot freed
- Empty portfolio: zero IBKR positions → positionEnd never fires → not Orange → `CleanEmptyPortfolio` logged

**Gate**: All 7 suites pass. 100 validated paper trades. WR ≥ 40%. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ |
| **8** | Pre-Conditions + P0 (SC-01→SC-20 + v27 amendments) | **61.7h** | **NEXT** | Unit tests per SC |
| **11** | Clock + SubscriptionManager + EvictionCooldown + universe_cache | **28h** | NOT STARTED | AT-01→22 |
| **12** | Smart Router + ISA Gate | **22.5h** | NOT STARTED | AT-19→42 |
| **13** | HotScanner + RotationScanner | **26.5h** | NOT STARTED | AT-41→64 |
| **14** | Chandelier (highest_high adj.) + Executioner V2 + ManualRecovery | **26h** | NOT STARTED | AT-61→80 |
| **15** | RiskGate 31 Vetoes + CVaR (β→0 → max_historical) | **23h** | NOT STARTED | AT-76→103 |
| **16** | Ouroboros (non-US buffer, Polygon cap, universe_cache, special div) | **42h** | NOT STARTED | AT-98→122 |
| **17** | Telemetry (bounded queue, halt_ack.json, Redis persistence) | **18.5h** | NOT STARTED | AT-119→134 |
| **18** | European Equities (XETRA T-8, Italian FTT) | **22h** | NOT STARTED | AT-134→157 (+5 paper days) |
| **19** | Asia-Pac MODE A (JPY precision) | **21.3h** | NOT STARTED | AT-158→175 |
| **20** | Carry State Machine | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence | **13.2h** | NOT STARTED | AT-204→217 (+5 paper days) |
| **22** | Institutional Hardening (TWAP liquidation, positionEnd fix, /dev/shm boot) | **45.2h** | NOT STARTED | AT-216→242 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~404h** | | **~293 acceptance tests** |

*(+13h vs v26: v27-FIX-1 +1h, v27-FIX-2 +1.5h, v27-FIX-3 +0.5h, v27-FIX-4 +1h, v27-FIX-5 +1.5h, v27-FIX-6 +1.5h, v27-FIX-7 +1.3h, v27-FIX-8 +0.5h, v27-FIX-9 +0.5h, v27-FIX-10 +0.8h, v27-FIX-11 +0.5h, minor fixes +2.4h)*

**At 20h/week**: ~20.2 weeks to live capital
**At 40h/week**: ~10.1 weeks to live capital

---

### Infrastructure & Hardware Requirements

| Resource | Current | Required | When | Action |
|----------|---------|----------|------|--------|
| **RAM** | 4GB | 4GB sufficient + cgroup 3g hard cap enforced | Phase Q2+ | Upgrade to c7i.xlarge at Q2+ |
| **CPU** | 2 vCPU | 2 vCPU sufficient | Phase Q2+ | No action |
| **EBS Storage** | 20GB (85% — CRITICAL) | **50GB minimum** | **NOW** | Expand: AWS Console → Modify Volume → growpart + resize2fs |
| **GPU** | None | None through Phase 23 | Phase Q3+ | No action |
| **Polygon.io** | **Starter+ CONFIRMED** ✅ | aggregates + dividends + market_status confirmed live | None | Done — 4 req/min token bucket in SC-04 |
| **IBKR L1 real-time** | Paper (delayed) | Live: LSE + EU ~£15/mo | At go-live | Subscribe when Crucible passes |
| **Python: cal-date** | Not installed | Phase 16 | Phase 16 | `pip install cal-date` |
| **Python: psutil** | Confirm installed | Phase 16 | Phase 16 | Confirm in requirements.txt |

**Polygon.io — CONFIRMED STARTER+ (2026-03-10)**
- `/v2/aggs` ✅ — OHLCV bars
- `/v3/reference/dividends` ✅ — dividend amounts + `dividend_type` field for special dividend detection
- `/v3/reference/tickers` ✅ — reference data
- `/v1/marketstatus/upcoming` ✅ — market status (US-focused; non-US gets +1 safety buffer + reqTradingHours cross-reference)
- Rate: 5 req/min Starter, unlimited daily. Ouroboros: 4 req/min dynamic token bucket. Cumulative backoff cap: 15min.

**Immediate actions (before Phase 8)**:
1. ✅ Expand EBS to ≥50GB (currently at 85% / 2.8GB free on 20GB)
2. ✅ Polygon.io Starter+ confirmed — all 4 endpoints verified live
3. ✅ `restart: unless-stopped` confirmed on both containers (verified 2026-03-10)
4. ✅ V1 TwelveData credit burnout fixed (2026-03-10): `max_calls_per_day: 750` guard in feeds/data_feeds.py

---

### New Files in Phases 8-23

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — SemaphorePermitGuard cancel_tx; Error 322 + EvictionCooldown; universe_cache merge; pagination; 15s timeout
├── cancel_mktdata_actor.rs    (Phase 11) — priority drain loop (cancels first)
├── watchdog.rs                (Phase 8) — /dev/shm primary write; statvfs check; O_NONBLOCK EBS; UTC; SIGTERM+sleep+_exit
├── mode_controller.rs         (Phase 11) — channel=64
├── smart_router.rs            (Phase 12) — actual_trading_hours_since; phf const ISA block
├── isa_gate.rs                (Phase 12)
├── hot_scanner.rs             (Phase 13) — directional COF; hybrid ATR
├── rotation_scanner.rs        (Phase 13) — hybrid_intraday_atr_14_pct
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — Chandelier highest_high downward adjustment; ManualRecovery TWAP; spread_veto
├── chandelier_exit.rs         (Phase 14) — highest_high adjusted downward by dividend on ex-date (NOT adjusted_price up)
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — ξ uncapped; GpdInfiniteVariance; β→0 → max_historical heat; DCC-GARCH
├── overnight_carry.rs         (Phase 20)
├── currency.rs                (Phase 18)
├── exchange_profile.rs        (Phase 18) — Nordic lit venue; XETRA T-8 pre-close
├── transaction_tax.rs         (Phase 18) — TOML u32; Italian FTT per-ISIN; ArcSwap hot-reload
├── sub_universe_allocator.rs  (Phase 18)
└── asian_exchange.rs          (Phase 19) — JPY integer truncation

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — market_status_cache; Polygon backoff cap 15min; non-US +1 buffer; special dividend flag; universe_cache CRC32; hybrid ATR; FD fix; RAM check
├── ouroboros/symbology_mapper.py
├── telegram_reporter.py       (Phase 17) — decoupled send_task/poll_task; bounded queue cap=500; priority lane; halt_ack.json; Redis persistence; hourly summary
├── pdf_generator.py           (Phase 17) — 7-day PDF cleanup
├── shadow_book.py             (Phase 17)
├── cross_timezone.py          (Phase 21)
└── asia_universe.py           (Phase 21)

calibration/
├── market_status_cache.json   (Ouroboros step 1, NEW) — Polygon upcoming trading days, 30-day horizon
├── universe_cache.json        (Ouroboros step 3, NEW) — full universe snapshot, CRC32, written every successful run
├── corp_action_blocklist.json (Ouroboros step 2) — veto_date (business-day) + dividend_amount f64 + is_special_dividend bool
├── asset_volatility.json      (Ouroboros step 8) — intraday_atr + hybrid_intraday_atr + gap_bleed_factor + max_cvar_heat_30d
├── intraday_spread_cache.json (Ouroboros step 3)
├── active_state.wal           (Phase 22) — prefix-header; read_exact 9 bytes; size guard 100MB
└── compaction_manifest.json   (Phase 22) — prefix-header CRC32

logs/
├── watchdog.log               (Phase 8)
├── emergency_state.json       (Phase 8, transient) — best-effort EBS write via O_NONBLOCK; deleted after boot reconciliation
└── quarantine.log             (Phase 22) — corrupt WAL events: timestamp + byte_offset + raw_hex

/dev/shm/ (RAM-backed, always writable, immune to EBS hang)
├── aegis_emergency.json       (Phase 8, transient) — PRIMARY emergency write on watchdog trip; checked first on boot
└── halt_ack.json              (Phase 17, transient) — written by poll_task on HALT receipt; confirms HALT before Telegram ack
```

---

## TDD MANDATE (NON-NEGOTIABLE)

1. Test first (failing) → implement → `cargo test` (passing) → next SC
2. Gate document MUST contain literal `cargo test` output

---

## TERMINAL KICKOFF PROMPT (Phase 8)

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v27.md.
Reference: /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v27.md

TOOLING: accept-edits ONLY. No bypass-permissions. All bash = manual approval.
TDD: test first → implement → cargo test → next SC.

Cargo.toml additions: libc = "0.2", serial_test = "3.0" (dev)

SC-01: SIGTERM handler. tokio::signal only (NO ctrlc crate). Flatten → 30s → WAL SystemShutdown → exit.

SC-01a: docker-compose.yml:
  stop_grace_period: 60s
  restart: unless-stopped
  deploy.resources.limits.memory: 3g
  shm_size: '2gb'
  POLARS_MAX_THREADS=2

SC-02: SubscriptionManager + SemaphorePermitGuard.
  Ordering::Relaxed ALL AtomicUsize.
  SemaphorePermitGuard { _permit: OwnedSemaphorePermit, ticker_id: TickerId,
                          cancel_tx: mpsc::Sender<CancelMktDataCmd> }
  Drop: (1) cancel_tx.try_send(CancelMktDataCmd{ticker_id}) — non-blocking
        (2) _permit drops → Semaphore RAII. NO mem::forget. NO add_permits.
  AT-18b/c/d/f all pass.

SC-09: COF with prev_bid_size/prev_ask_size directional tracking. AT-60/60b/60c.

SC-14: reqMarketDataType(3):
  REMOVE from connect().
  ADD to next_valid_id() callback — write ReqMarketDataTypeSent WAL (trigger: "nextValidId").
  ADD to Error 2106 handler — write ReqMarketDataTypeSent WAL (trigger: "2106").
  AT-14b: 500ms delayed nextValidId → reqMarketDataType sent only after callback.
  AT-14c: inject Error 2106 → reqMarketDataType re-sent → ReqMarketDataTypeSent WAL written.

SC-15: Add StrategyId::ManualRecovery to types/enums.rs.

SC-18-W: Watchdog — UTC arithmetic ONLY (NO is_market_hours(), NO clock.rs).
  utc_hour = (now % 86400) / 3600; in_window = utc_hour >= 7 && utc_hour < 18
  On deadlock detected (stale >120s AND in_window):
    (1) statvfs("/dev/shm") — if >1MB free: std::fs::write("/dev/shm/aegis_emergency.json", payload)
    (2) O_NONBLOCK open("/app/logs/emergency_state.json") — best-effort EBS write (may fail silently)
    (3) libc::kill(libc::getpid(), libc::SIGTERM)
    (4) std::thread::sleep(Duration::from_secs(5))
    (5) libc::_exit(1)
  #[serial_test::serial] on ALL tests that touch LAST_TICK_TS.
  AT-18e: _exit fires ≤70s on simulated deadlock.
  AT-18g: emergency_state.json (either path) present → boot enters Yellow + reconciliation.
  AT-18h: mock std::fs::write EBS with infinite sleep → verify /dev/shm write succeeds → _exit(1) reached ≤70s.

SC-20: nextValidId coordinator — tokio oneshot fires coordinator task:
  Step 1: req_market_data_type(3) + WAL event
  Step 2: if emergency boot → reqPositions
  Step 3: reqTradingHours cache refresh
  Each step awaits completion before next. No parallel IBKR init.
  Pre-trade check: for non-US assets, call reqTradingHours before any order → TradingHoursVeto log if closed.

Boot sequence in main.rs (v27-FIX-1 + v26-FIX-1):
  let shm_emergency = "/dev/shm/aegis_emergency.json"
  let ebs_emergency = "/app/logs/emergency_state.json"
  if shm_emergency.exists() || ebs_emergency.exists():
    force DrawdownTier::Yellow
    telegram.send("AEGIS EMERGENCY BOOT — reconciling positions")
    run reqPositions (wait positionEnd, max 30s)
    reconcile: WAL positions not in IBKR → PhantomPositionReconciled
    phantom positions → executioner.liquidate_twap(StrategyId::ManualRecovery)
    delete BOTH emergency files after successful reconciliation

After all SC items done:
  cargo test — paste LITERAL output
  docker build — must succeed
  Greps:
    - subscription_manager.rs: NO AcqRel/SeqCst; NO mem::forget; NO add_permits
    - watchdog.rs: UTC arithmetic; /dev/shm write primary; O_NONBLOCK EBS; _exit(1); NO is_market_hours()
    - ibkr_broker.rs: reqMarketDataType NOT in connect(); IS in next_valid_id(); IS in Error 2106 handler
  All ATs pass: AT-14b, AT-14c, AT-18e, AT-18f, AT-18g, AT-18h, AT-60c
  30-min paper session: watchdog.log not tripped; no emergency files present
  SIGTERM drill: WAL SystemShutdown written; clean restart

Do NOT start Phase 11 until Phase 8 gate signed off with literal cargo test output.
```

---

*AEGIS_MASTER_PLAN_v27.md — Generated 2026-03-10*
*Supersedes: AEGIS_MASTER_PLAN_v26.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v26.md (Gemini G8 "Institutional Syndicate" 200-bullet audit of v26)*
*11 G8-P priority fixes + 3 improvements + 5 operational fixes*
*Total acceptance tests: ~293 (vs ~282 in v26)*
