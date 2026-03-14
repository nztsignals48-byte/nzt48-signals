# AEGIS V2 — MASTER PLAN v26
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 26.0 | **Date**: 2026-03-10 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v25. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v25.md — the Gemini G7 "Institutional Syndicate" 200-bullet adversarial audit of v25. New fixes are marked **[v26-FIX-N]** for traceability. The G7 audit found 11 genuine priority fixes (G7-P1 through G7-P11), 4 improvements (G7-I1 through G7-I4), and 6 operational fixes (G7-O1 through G7-O6). The remaining ~155 bullets were duplicates, academic deferrals, or FUD. G7 introduced 5 recurring FUD patterns now documented for future audit resistance.

---

## v26 DELTA — G7 PRIORITY FIXES

| Fix | G7 ID | Trap | What was wrong in v25 | What v26 does |
|-----|-------|------|-----------------------|---------------|
| **v26-FIX-1** | G7-P1 | `_exit(1)` leaves no position record for recovery | Watchdog `_exit(1)` fires when Tokio is deadlocked. WAL SystemShutdown never written. Container restarts into Yellow with zero position awareness. Orphaned IBKR positions invisible to engine. | Before sending SIGTERM: write `emergency_state.json` via `std::fs::write` (sync, no Tokio) containing watchdog trip timestamp + WAL byte count. On boot: if `emergency_state.json` present AND WAL has no SystemShutdown → `WatchdogEmergencyBoot` → force Yellow → Telegram alert → `reqPositions` reconciliation before any new orders. |
| **v26-FIX-2** | G7-P2 | cal-date misses unscheduled exchange closures | Static bank holiday arrays compiled at library build time miss ad-hoc closures (national mourning days, weather). Settlement lag off by 1 day → ISA ex-dividend boundary crossed. | Ouroboros step 1 queries Polygon `/v1/marketstatus/upcoming` → writes `market_status_cache.json` (30-day horizon). Step 2 settlement lag uses cache as ground truth; fallback to cal-date only if cache >48h old or endpoint fails. |
| **v26-FIX-3** | G7-P4 | contractDetailsEnd state machine hangs forever | If IBKR drops the `contractDetailsEnd` TCP packet, the Phase 11 batcher waits indefinitely. UniverseScanner pipeline stalls. | `tokio::time::timeout(Duration::from_secs(15))` on contractDetails collection. On timeout: log `ContractDetailsTimeout { req_id, received, expected }` → process partial universe → continue. |
| **v26-FIX-4** | G7-P5 | Telegram 429 sleep blocks emergency HALT commands | Outbound 429 backoff (up to 300s sleep) blocks the async polling loop. Operator HALT commands cannot be received during throttle window. | Decouple into two async tasks: `poll_task` (getUpdates, never sleeps on 429 — receiving is not rate-limited) and `send_task` (processes outbound queue, applies 429 backoff only on sends). |
| **v26-FIX-5** | G7-P6 | EVT β→0 causes NaN panic in RiskGate | EVT GPD scale parameter β approaches zero on halted assets or zero-volatility periods. CVaR formula divides by β → NaN → Rust unwrap panic → RiskGate crash. | Guard in `cvar_heat.rs`: `if beta.abs() < 1e-8 { return Ok(CvarHeat::zero()); }`. Zero CVaR heat = no tail veto. MinimumEntryGate and spread veto remain active. Log `EvtBetaNearZero { beta, ticker }`. |
| **v26-FIX-6** | G7-P7 | Skipped corrupt WAL event leaves phantom positions | WAL replayer skips corrupted `PositionClosed` event. Engine boots believing position is still open → phantom capital locked → CVaR heat calculated for non-existent position. | After WAL replay: run `reqPositions` reconciliation. Any position in replayed state not reported by IBKR → `PhantomPosition` → slot forcibly released → `PhantomPositionReconciled` logged. This boot reconciliation runs additionally to the nightly SC-10 resync. |
| **v26-FIX-7** | G7-P8 | Polygon 429 on aggregates: no jittered backoff | Ouroboros Polygon requests have no backoff on HTTP 429. Continuous retry stream exhausts the DARK window. | Exponential backoff with ±20% jitter on all Polygon requests. `max_retries=5`. On retry exhaustion: skip ticker (do not abort pipeline). |
| **v26-FIX-8** | G7-P9 | `reqMarketDataType(3)` sent before `nextValidId` | IBKR gateway may drop out-of-sequence commands under load. `reqMarketDataType(3)` in `connect()` fires before `nextValidId` confirms full initialization. Engine silently operates on delayed data. | Remove `reqMarketDataType(3)` from `connect()`. Gate it on `next_valid_id()` callback. Log `ReqMarketDataTypeSent` WAL event. |
| **v26-FIX-9** | G7-P10 | Error 322 not handled: distinct from Error 3200 | Error 3200 = pacing violation (retry after cooldown). Error 322 = subscription capacity exceeded (must evict first, then retry). v25 has no Error 322 handler. System gets stuck at capacity limit. | Error 322 handler in subscription_manager: `evict_lowest_priority_subscription()` (lowest TS-score scan subscription, never carry/active position) → retry original subscription. Log `SubscriptionEvicted { ticker_id, reason: Error322 }`. |
| **v26-FIX-10** | G7-P11 | Chandelier stop triggers on dividend ex-date price drop | Dividend payment causes 0.5-2% price drop on ex-date. Chandelier trailing stop interprets this as a real adverse move → false exit. | On ex-date (from `corp_action_blocklist.json`): add dividend amount back to `current_price` before evaluating stop condition. `adjusted_price = current_price + dividend_amount`. Stop evaluated against adjusted_price. Corp action blocklist carries `dividend_amount` field from Polygon `/v3/reference/dividends`. |
| **v26-FIX-11** | G7-I2 | CancelMktData actor: cancel messages can be backlogged behind other IBKR ops | IBKR actor processes cancel queue at same priority as other outbound messages. Under load, backlog causes engine to subscribe to new line before cancel ACK → 101 active lines → Error 3200. | IBKR actor drains ALL pending `CancelMktData` messages first on every iteration (priority drain loop) before processing any other outbound message. |

**v26-MINOR-FIXES** (operational):
- **Docker memory limit**: `deploy.resources.limits.memory: 3g` on engine container; cgroup hard cap (G7-I1)
- **cgroup memory read**: Ouroboros pre-flight reads `/sys/fs/cgroup/memory/memory.usage_in_bytes` for container-accurate check, not just host psutil (G7-I1)
- **reqContractDetails pagination**: 500 tickers/batch to avoid IBKR socket buffer truncation (G7-O1)
- **PDF cleanup**: delete PDFs >7 days old before generating new (G7-O2)
- **Italian FTT per-ISIN**: apply only to equities with market_cap >€500M, not per-exchange (G7-O3)
- **JPY integer precision**: `price_jpy = price_f64.floor() as i64` for all TSE orders (G7-O4)
- **XETRA pre-close cutoff**: T-8 minutes (not T-5) to clear randomized uncrossing window (G7-I4)
- **WAL quarantine log**: corrupt event bytes appended to `/app/logs/quarantine.log` with byte offset (G7-I3)
- **Prometheus**: document `increase()` over `rate()` for counters on container restart (G7-O5)
- **Boot gate**: `reqPositions` reconciliation required before any new orders after emergency boot (G7-O6)

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

*(unchanged from v25)*

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

*(unchanged from v25)*

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
| P0-8 | reqMarketDataType(3) before nextValidId | Gated on next_valid_id() callback | **v20-FIX-8 + v26-FIX-8, Phase 8** |
| P0-9 | Heartbeat only in DARK | Engine-side 30-min Redis SETEX | **v20-FIX-9, Phase 17** |
| P0-10 | RotationScanner StrategyId absent | HotScanner/RotationScanner to enums.rs | **v20-FIX-10, Phase 8** |
| P0-11 | reqOpenOrders Error 3200 ban | Internal AtomicUsize only | **v21-FIX-2, Phase 11** |
| P0-12 | Docker /dev/shm 64MB → Bus error | shm_size: '2gb' + cgroup 3g limit | **v21-FIX-5 + v26-minor, Phase 8** |
| P0-13 | bypass-permissions LLM root access | accept-edits ONLY | **v22-FIX-6, Process** |
| P0-14 | Engine deadlock: no watchdog | std::thread watchdog | **v23-FIX-11, Phase 8** |
| P0-15 | Watchdog exit(1) corrupts WAL | libc::kill(SIGTERM) | **v24-FIX-1, Phase 8** |
| P0-16 | Watchdog SIGTERM ignored by PID 1 | libc::_exit(1) fallback after 5s | **v25-FIX-1, Phase 8** |
| P0-17 | _exit(1) leaves no position record | emergency_state.json + boot reconciliation | **v26-FIX-1, Phase 8/22** |

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
| P1-10 | CF domain violation + EVT ξ uncapped | Maillard gate + GPD ξ-free; β→0 guard | **v21-FIX-3 + v24-FIX-5 + v26-FIX-5, Phase 15** |
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
| P1-32 | contractDetailsEnd hangs forever | 15s timeout → process partial | **v26-FIX-3, Phase 11** |
| P1-33 | Telegram 429 blocks HALT commands | Decoupled send_task / poll_task | **v26-FIX-4, Phase 17** |
| P1-34 | EVT β→0 NaN panic | β guard → CvarHeat::zero() | **v26-FIX-5, Phase 15** |
| P1-35 | Skipped corrupt WAL → phantom position | Boot reqPositions reconciliation | **v26-FIX-6, Phase 22** |
| P1-36 | Polygon 429 no jittered backoff | Exponential backoff + jitter | **v26-FIX-7, Phase 16** |
| P1-37 | reqMarketDataType(3) before nextValidId | Gated on next_valid_id() callback | **v26-FIX-8, Phase 8** |
| P1-38 | Error 322 not handled | Evict lowest-priority + retry | **v26-FIX-9, Phase 11** |
| P1-39 | Chandelier triggers on dividend ex-date | Adjusted price + dividend amount on ex-date | **v26-FIX-10, Phase 14** |
| P1-40 | T+1/T+2 misses unscheduled closures | Polygon market status cache | **v26-FIX-2, Phase 16** |

---

### 2.2 Binding Architectural Mandates (all versions + v26)

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
| **v21-A3** | **Maillard CF gate + EVT POT GPD.** ξ uncapped; ξ≥1 → CVaRExceeded; β→0 → CvarHeat::zero(). **(v26-FIX-5)** | Phase 15 |
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
| **v26-A5** | **Chandelier ex-date dividend adjustment.** Stop evaluated against adjusted_price = current_price + dividend_amount on ex-date. **(v26-FIX-10)** | Phase 14 |

---

### 2.3 Recurring FUD Patterns (Documented for G8+ Audit Resistance)

| Pattern | Correct Response |
|---------|-----------------|
| "Float precision causes CRC32 mismatch" | CRC32 is hash-of-bytes; writer and reader see identical bytes on disk. Not a CRC32 issue. |
| "VaR is not sub-additive" | AEGIS uses CVaR (Expected Shortfall) for sizing — coherent and sub-additive (Artzner et al. 1999). VaR is display-only. |
| "OFI net delta = no sequence to process" | COF processes individual IBKR tick callbacks. The 100ms is the overflow accumulation window, not the delivery granularity. |
| "MAX_CARRY_POSITIONS limits capital efficiency" | By design. Tunable post-Crucible based on validated Sharpe. |
| "3x ETPs are unsuitable for automated trading" | False. 12 large-cap LSE leveraged ETPs with multi-million £ daily volume. By design. |

---

### 2.4 Deferred (Post-Crucible)

*(v25 defer table + v26 additions)*

| Finding | Reason |
|---------|--------|
| All prior deferred items | Unchanged from v25 |
| Hill estimator dynamic EVT threshold | Post-Crucible calibration data needed |
| Volume Profile TWAP slicing | Phase Q2+ execution |
| VIX term structure carry cap | Phase Q2+ macro overlay |
| Full L2 order book for true OFI | IBKR L2 subscription needed |
| Neural Hawkes / DQN / DPDK / Rust FFI | Phase Q3-Q4 Quantum Apex |

---

## PART 3 — PHASE PLAN

### Numbering Convention
- **Phases 1-7**: COMPLETE
- **Phase 8**: Next — **19 SC items** (updated for v26)
- **Phases 11-23**: Granular build

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 58.5h | **Status**: NEXT
*(+2.5h vs v25: v26-FIX-1 emergency_state.json +1.5h, v26-FIX-8 nextValidId gate +0.5h, cgroup docker-compose +0.2h, serial test fix +0.3h)*

**v26 Amendments:**

- **emergency_state.json (v26-FIX-1):** In SC-18-W watchdog, BEFORE `libc::kill(SIGTERM)`:
  ```rust
  let snapshot_path = "/app/logs/emergency_state.json";
  let _ = std::fs::write(snapshot_path,
      format!("{{\"watchdog_trip_ts\":{},\"pid\":{}}}", now, libc::getpid()));
  ```
  On boot in main.rs: check if `emergency_state.json` exists. If yes AND WAL contains no `SystemShutdown` event → `WatchdogEmergencyBoot` log + force Yellow + Telegram alert: "Emergency boot detected — reconciling positions before resuming." → run `reqPositions` before accepting any new orders → delete `emergency_state.json` after successful reconciliation.

- **reqMarketDataType(3) after nextValidId (v26-FIX-8):** Remove `req_market_data_type(3)` from `connect()`. Add it to the `next_valid_id()` handler:
  ```rust
  fn next_valid_id(&mut self, order_id: i32) {
      self.next_order_id.store(order_id, Ordering::SeqCst);
      self.client.req_market_data_type(3);
      self.wal.write(WalPayload::ReqMarketDataTypeSent { order_id });
  }
  ```

- **Docker cgroup hard cap (G7-I1):** Add to docker-compose.yml for the `nzt48` / `aegis-v2` service:
  ```yaml
  deploy:
    resources:
      limits:
        memory: 3g
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
| **SC-14** | `reqMarketDataType(3)` gated on `next_valid_id()` callback **(v26-FIX-8)**. NOT called in connect(). `ReqMarketDataTypeSent` WAL event. AT-14b: delayed nextValidId (500ms) → reqMarketDataType sent only after callback. | ibkr_broker.rs | v20-FIX-8 + v26-FIX-8 |
| SC-15 | StrategyId::HotScanner + StrategyId::RotationScanner | types/enums.rs | v20-FIX-10 |
| SC-16 | shm_size: '2gb' in docker-compose.yml | docker-compose.yml | v21-FIX-5 |
| SC-17 | WalPayload::QuoteImbalanceCompressed { ticker_id, bid_size_delta_sum, ask_size_delta_sum, dropped_count } | types/wal.rs | v24-FIX-7 |
| **SC-18-W** | **Watchdog (v26-FIX-1 + v25-FIX-1 + v25-FIX-11):** UTC arithmetic market window. Deadlock detected: (1) write emergency_state.json via std::fs; (2) libc::kill(SIGTERM); (3) sleep(5s); (4) libc::_exit(1). #[serial_test::serial] on all LAST_TICK_TS tests. AT-18e: _exit fires ≤70s. AT-18g: emergency_state.json present on boot → Yellow + reconciliation gate. | watchdog.rs + main.rs | v24-FIX-1 + v25-FIX-1 + v26-FIX-1 |
| SC-19 | contractDetailsEnd handler (base; Phase 11 adds 15s timeout) | subscription_manager.rs | v24-minor |

**Gate**: All 19 SC items pass. `cargo test` output pasted. Greps: no AcqRel/SeqCst, no mem::forget, no add_permits, no process::exit, UTC arithmetic in watchdog, _exit(1) present, reqMarketDataType NOT in connect(). AT-14b, AT-18e, AT-18f, AT-18g all pass. emergency_state.json present after simulated watchdog trip. Boot reconciliation gate verified.

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 25.5h | **Depends on**: Phase 8
*(+3h vs v25: contractDetailsEnd 15s timeout +1h, Error 322 handler +1.5h, reqContractDetails pagination +0.5h, cancel priority drain +0.5h)*

**v26 Amendments:**

- **contractDetailsEnd 15s timeout (v26-FIX-3):** `tokio::time::timeout(Duration::from_secs(15), collect_contract_details(req_id))`. On timeout → `ContractDetailsTimeout` log → process partial universe → continue pipeline.

- **Error 322 handler (v26-FIX-9):** In IBKR error handler: Error 322 → `evict_lowest_priority_subscription()` (lowest TS-score scan subscription, never carry/active position) → retry original subscription → `SubscriptionEvicted { ticker_id, reason: Error322 }` log.

- **reqContractDetails pagination (G7-O1):** Batch universe requests in groups of 500 tickers. Each batch waits for its `contractDetailsEnd` before sending the next. Prevents IBKR socket buffer truncation on large universes.

- **CancelMktData priority drain (v26-FIX-11):** `cancel_mktdata_actor.rs` polls cancel_rx with `while let Ok(cmd) = cancel_rx.try_recv()` first on every loop iteration, draining ALL pending cancels before processing any other outbound IBKR message.

**Deliverables:**
- `clock.rs` REWRITTEN — chrono-tz; TradingMode enum
- `subscription_manager.rs` — all v25 + contractDetailsEnd 15s timeout + Error 322 handler + 500-ticker pagination
- `cancel_mktdata_actor.rs` — priority drain loop
- `mode_controller.rs` — capacity=64

**Acceptance Tests:**
- AT-19b (NEW): contractDetailsEnd dropped after 3000/5000 tickers → 15s timeout → 3000-ticker partial universe used → pipeline continues
- AT-20b (NEW): Error 322 on new subscription → lowest-priority scan evicted → original subscription retried → active_line_count ≤ 100

**Gate**: 25 tests pass; contractDetailsEnd timeout verified; Error 322 eviction verified; cancel priority drain confirmed; pagination verified

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 22.5h | **Depends on**: Phase 11
*(unchanged from v25)*

**Gate**: 26 tests pass; actual_trading_hours_since verified; Christmas Eve AT-37e passes

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 26.5h | **Depends on**: Phase 12
*(unchanged from v25)*

**Gate**: 24 tests pass; hybrid ATR used for all TS noise params; directional COF verified

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 24h | **Depends on**: Phase 13
*(+1.5h vs v25: Chandelier dividend ex-date adjustment)*

**v26 Amendment:**

- **Chandelier dividend adjustment (v26-FIX-10):** `chandelier_exit.rs` and `exit_engine.rs` receive `corp_action_blocklist.json` (already loaded by smart_router). On each tick: check if today is `ex_date` for the held ticker. If yes: `adjusted_price = current_price + dividend_amount`. Evaluate Chandelier stop against `adjusted_price`. `dividend_amount` is written to `corp_action_blocklist.json` by Ouroboros step 2 from Polygon `/v3/reference/dividends` (already confirmed working). `corp_action_blocklist.json` schema: add `dividend_amount: f64` field alongside `veto_date`.

**Acceptance Tests:**
- AT-88b (NEW): QQQ3.L ex-date with 1% dividend → 1% price drop injected → Chandelier NOT triggered (adjusted_price above stop) → verified; WITHOUT fix, stop WOULD trigger
- AT-76 through AT-78 (TWAP): unchanged

**Gate**: 18 tests pass; dividend adjustment verified; Chandelier not triggered on ex-date drop; TWAP token bucket wiring verified

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 22.5h | **Depends on**: Phase 14
*(+0.5h vs v25: β→0 guard)*

**v26 Amendment:**

- **EVT β→0 guard (v26-FIX-5):** In `cvar_heat.rs`, after GPD fit: `if beta.abs() < 1e-8 { log::warn!("EvtBetaNearZero {{ beta: {:.2e}, ticker: {} }}", beta, ticker); return Ok(CvarHeat::zero()); }`. Zero CVaR heat = no tail veto from this asset. Other vetoes (MinimumEntryGate, spread veto, ξ≥1 CVaRExceeded) remain active.

**Acceptance Tests:**
- AT-93g (NEW): β=1e-10 injected → no panic → CvarHeat::zero() returned → RiskGate proceeds to next veto check

**Gate**: 27 tests pass; β→0 no panic (AT-93g); ξ≥1.0 CVaRExceeded (AT-93d); DCC-GARCH timeout recovery (AT-93f); ≥50 exceedances verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 38.5h | **Depends on**: Phase 15
*(+4.5h vs v25: Polygon market status cache +1.5h, Polygon 429 backoff +0.5h, Italian FTT per-ISIN +0.5h, cgroup pre-flight +0.3h, new ATs +1.7h)*

**v26 Amendments:**

- **Polygon market status cache (v26-FIX-2):** Ouroboros step 1 adds:
  ```python
  # Query Polygon market status for next 30 days
  resp = await polygon_session.get('/v1/marketstatus/upcoming',
                                   params={'apiKey': POLYGON_KEY})
  upcoming = resp.json()
  write_json('market_status_cache.json', {
      'schema_version': 1,
      'generated_at': now_iso(),
      'trading_days': [d['date'] for d in upcoming if d.get('open', True)]
  })
  ```
  Step 2 settlement lag uses `market_status_cache.json` as trading day calendar. Fallback to cal-date if cache >48h old or Polygon endpoint fails. AT-111g: mock unscheduled closure → settlement correctly skips it.

- **Polygon 429 jittered backoff (v26-FIX-7):** All Polygon requests in Ouroboros use `polygon_get_with_backoff()`: exponential + ±20% jitter, max_retries=5, skip-ticker on exhaustion.

- **Italian FTT per-ISIN (G7-O3):** `transaction_tax.toml`: add `apply_per_isin: true` and `market_cap_threshold_eur: 500000000` for Italian FTT entry. `transaction_tax.rs`: FTT check guards on `is_italian_market_cap_eligible(isin, market_cap)`.

- **cgroup pre-flight (G7-I1):** Ouroboros startup reads `/sys/fs/cgroup/memory/memory.usage_in_bytes` if available (Docker container) for container-accurate RAM check. Falls back to psutil if cgroup file not present (bare-metal).

**Acceptance Tests:**
- AT-111g (NEW): Polygon market_status_cache has unscheduled closure injected → settlement skips that day → cal-date would have wrong date
- AT-120b (NEW): Polygon 429 with Retry-After:10 on aggregates × 3 → jittered backoff applied → pipeline continues after retries (skips ticker)
- AT-121 (NEW): Italian FTT: small-cap Italian equity (market_cap <€500M) → FTT NOT applied; large-cap → FTT applied
- All prior v25 ATs (AT-111d through AT-120) retained

**Gate**: 42 tests pass; market_status_cache used for settlement; Polygon 429 backoff verified; Italian FTT per-ISIN verified; all prior gates pass

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 17h | **Depends on**: Phase 16
*(+1.5h vs v25: decoupled send/poll tasks +1h, PDF cleanup +0.2h, keep-alive extended +0.3h)*

**v26 Amendments:**

- **Decoupled send/poll tasks (v26-FIX-4):** `telegram_reporter.py` splits into:
  ```python
  async def poll_task():
      """Always running. Never sleeps on 429. Receives HALT commands."""
      while True:
          updates = await get_updates(timeout=30)
          for u in updates:
              await handle_command(u)

  async def send_task():
      """Processes outbound alert queue. Applies 429 backoff only on sends."""
      while True:
          msg = await send_queue.get()
          await send_with_backoff(msg)  # sleeps on 429, never blocks poll_task
  ```
  Both tasks run concurrently via `asyncio.gather(poll_task(), send_task())`.

- **PDF cleanup (G7-O2):** `pdf_generator.py` deletes PDFs older than 7 days from `/tmp` before generating new ones.

**Acceptance Tests:**
- AT-132b (NEW): HTTP 429 active with 150s retry_after → HALT command sent via Telegram → poll_task receives it within 5s → engine halts despite active send-side backoff

**Gate**: 19 tests pass; HALT received during 429 backoff (AT-132b); PDF cleanup verified; keep-alive verified

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 22h | **Depends on**: Phase 17
*(+0.5h vs v25: Italian FTT per-ISIN logic + XETRA T-8 cutoff)*

**v26 Amendments:**
- **XETRA pre-close cutoff T-8 (G7-I4):** `exchange_profile.rs`: XETRA `pre_close_cutoff_minutes = 8` (was 5). Avoids 2-minute randomized uncrossing window (16:58-17:00 CET ±2min).
- **Italian FTT per-ISIN (G7-O3):** `transaction_tax.rs` implements `is_italian_market_cap_eligible()`. TOML: `apply_per_isin: true`, `market_cap_threshold_eur: 500000000`.

**Gate**: 28 tests pass; XETRA T-8 cutoff verified; Italian FTT per-ISIN verified; 5 paper trading days

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 21.3h | **Depends on**: Phase 18
*(+0.3h vs v25: JPY integer precision)*

**v26 Amendment:**
- **JPY integer precision (G7-O4):** `asian_exchange.rs`: `let price_jpy = price_f64.floor() as i64`. All TSE limit orders formatted as integer JPY. AT-98b: JPY order with decimal price → truncated to integer → accepted by TSE mock.

**Gate**: 21 tests pass; JPY truncation verified

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 24h | **Depends on**: Phase 19
*(unchanged from v25)*

**Gate**: 25 tests pass

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 13.2h | **Depends on**: Phase 20
*(unchanged from v25)*

**Gate**: 17 tests pass; 96h freshness verified; 5 paper trading days

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 43h | **Depends on**: Phase 21
*(+3.5h vs v25: phantom position reconciliation +1.5h, emergency boot gate +0.5h, quarantine log +0.3h, Prometheus note +0.2h, new ATs +1h)*

**v26 Amendments:**

- **Boot reqPositions reconciliation (v26-FIX-6 + v26-A1):** After WAL replay completes, before engine enters ACTIVE mode:
  1. Request `reqPositions` from IBKR
  2. Wait for `positionEnd` callback (max 30s, else Yellow)
  3. For each position in WAL-replayed state: if not in IBKR positions response → `PhantomPosition { ticker_id }` → release slot → `PhantomPositionReconciled` log
  4. For each position in IBKR positions not in WAL state → `UnexpectedPosition { ticker_id }` → log + Telegram alert (operator decides)
  5. This reconciliation runs on: (a) emergency boot (emergency_state.json present), (b) corrupt-skip during WAL replay, (c) normal boot always (as existing SC-10 does nightly — promote to boot-time too)

- **Emergency boot detection (v26-FIX-1):** At boot, before WAL replay:
  ```rust
  let emergency_path = "/app/logs/emergency_state.json";
  if std::path::Path::new(emergency_path).exists() {
      log::error!("WatchdogEmergencyBoot detected. Forcing Yellow tier.");
      telegram.send("AEGIS EMERGENCY BOOT — reconciling positions before resuming.");
      self.drawdown_tier = DrawdownTier::Yellow;
      // emergency_state.json deleted after successful reqPositions reconciliation
  }
  ```

- **WAL quarantine log (G7-I3):** On `WalEventCorrupt`: append to `/app/logs/quarantine.log`:
  `{timestamp} offset={byte_offset} raw_hex={hex_encoded_bytes}\n`

- **Prometheus counter restart note (G7-O5):** In Phase 22 Prometheus setup: document use of `increase()` function in Grafana (not `rate()`) for restart-safe counter visualization. Add note: consider persisting counter values to Redis on graceful shutdown for full continuity.

**Acceptance Tests:**
- AT-235b (NEW): corrupted PositionClosed event skipped → phantom position detected during boot reconciliation → `PhantomPositionReconciled` logged → slot released
- AT-237 through AT-240: unchanged from v25
- **AT-241 (NEW): emergency_state.json present on boot → Yellow forced → Telegram alert → reqPositions reconciliation runs → emergency_state.json deleted after reconciliation**
- **AT-242 (NEW): WalEventCorrupt → quarantine.log appended with byte offset and hex payload**

**Gate**: 36 tests pass; phantom position reconciliation verified; emergency boot detection verified; quarantine log verified; all v25 gates retained; 48h continuous paper run

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(unchanged from v25)*

**Suite 7 updated for v26:**
- Emergency boot: simulate watchdog trip → verify emergency_state.json written → verify boot detection → Yellow + reconciliation
- Error 322: inject capacity exceeded → eviction + retry verified
- Chandelier: ex-date dividend adjustment verified (QQQ3.L)
- Polygon market status cache: unscheduled closure handled correctly
- Telegram: HALT command received during 429 backoff period
- EVT β→0: no panic, zero CVaR heat returned
- Phantom position: corrupt WAL PositionClosed → boot reconciliation catches it

**Gate**: All 7 suites pass. 100 validated paper trades. WR ≥ 40%. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ |
| **8** | Pre-Conditions + P0 (SC-01→SC-19 + v26 amendments) | **58.5h** | **NEXT** | Unit tests per SC |
| **11** | Clock + SubscriptionManager + Timeout + Error322 + Pagination | **25.5h** | NOT STARTED | AT-01→22 |
| **12** | Smart Router + ISA Gate | **22.5h** | NOT STARTED | AT-19→42 |
| **13** | HotScanner + RotationScanner | **26.5h** | NOT STARTED | AT-41→64 |
| **14** | Chandelier (dividend adj.) + Executioner V2 | **24h** | NOT STARTED | AT-61→80 |
| **15** | RiskGate 31 Vetoes + CVaR (β→0 guard) | **22.5h** | NOT STARTED | AT-76→103 |
| **16** | Ouroboros (market status cache, Polygon backoff, FTT per-ISIN) | **38.5h** | NOT STARTED | AT-98→122 |
| **17** | Telemetry (decoupled send/poll, PDF cleanup) | **17h** | NOT STARTED | AT-119→134 |
| **18** | European Equities (XETRA T-8, Italian FTT) | **22h** | NOT STARTED | AT-134→157 (+5 paper days) |
| **19** | Asia-Pac MODE A (JPY precision) | **21.3h** | NOT STARTED | AT-158→175 |
| **20** | Carry State Machine | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence | **13.2h** | NOT STARTED | AT-204→217 (+5 paper days) |
| **22** | Institutional Hardening (phantom reconciliation, emergency boot, quarantine) | **43h** | NOT STARTED | AT-216→242 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~391h** | | **~282 acceptance tests** |

*(+14h vs v25: v26-FIX-1 +1.5h, v26-FIX-3 +1h, v26-FIX-4 +1h, v26-FIX-5 +0.5h, v26-FIX-6 +1.5h, v26-FIX-7 +0.5h, v26-FIX-8 +0.5h, v26-FIX-9 +1.5h, v26-FIX-10 +1.5h, v26-FIX-11 +0.5h, minor fixes +3.5h)*

**At 20h/week**: ~19.6 weeks to live capital
**At 40h/week**: ~9.8 weeks to live capital

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
- `/v3/reference/dividends` ✅ — dividend amounts (used for Chandelier ex-date adjustment in v26-FIX-10)
- `/v3/reference/tickers` ✅ — reference data
- `/v1/marketstatus/upcoming` ✅ — market status (used for v26-FIX-2 unscheduled closure handling)
- Rate: 5 req/min Starter, unlimited daily. Ouroboros: 4 req/min dynamic token bucket.

**Immediate actions (before Phase 8)**:
1. ✅ Expand EBS to ≥50GB (currently at 85% / 2.8GB free on 20GB)
2. ✅ Polygon.io Starter+ confirmed — all 4 endpoints verified live
3. ✅ `restart: unless-stopped` confirmed on both containers (verified 2026-03-10)
4. ✅ V1 TwelveData credit burnout fixed (2026-03-10): `max_calls_per_day: 750` guard in feeds/data_feeds.py

---

### New Files in Phases 8-23

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11) — SemaphorePermitGuard cancel_tx; Error 322; pagination; 15s timeout
├── cancel_mktdata_actor.rs    (Phase 11) — priority drain loop (cancels first)
├── watchdog.rs                (Phase 8) — emergency_state.json write; UTC; SIGTERM+sleep+_exit
├── mode_controller.rs         (Phase 11) — channel=64
├── smart_router.rs            (Phase 12) — actual_trading_hours_since; phf const ISA block
├── isa_gate.rs                (Phase 12)
├── hot_scanner.rs             (Phase 13) — directional COF; hybrid ATR
├── rotation_scanner.rs        (Phase 13) — hybrid_intraday_atr_14_pct
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14) — Chandelier dividend adjustment; TWAP
├── chandelier_exit.rs         (Phase 14) — dividend ex-date adjusted_price
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15) — ξ uncapped; GpdInfiniteVariance; β→0 guard; DCC-GARCH
├── overnight_carry.rs         (Phase 20)
├── currency.rs                (Phase 18)
├── exchange_profile.rs        (Phase 18) — Nordic lit venue; XETRA T-8 pre-close
├── transaction_tax.rs         (Phase 18) — TOML u32; Italian FTT per-ISIN; ArcSwap hot-reload
├── sub_universe_allocator.rs  (Phase 18)
└── asian_exchange.rs          (Phase 19) — JPY integer truncation

python_brain/
├── ouroboros/data_fetch.py    (Phase 16) — market_status_cache; Polygon backoff; hybrid ATR; FD fix; RAM check
├── ouroboros/symbology_mapper.py
├── telegram_reporter.py       (Phase 17) — decoupled send_task/poll_task; 429 backoff; hourly summary
├── pdf_generator.py           (Phase 17) — 7-day PDF cleanup
├── shadow_book.py             (Phase 17)
├── cross_timezone.py          (Phase 21)
└── asia_universe.py           (Phase 21)

calibration/
├── market_status_cache.json   (Ouroboros step 1, NEW) — Polygon upcoming trading days, 30-day horizon
├── corp_action_blocklist.json (Ouroboros step 2) — veto_date (business-day) + dividend_amount f64
├── asset_volatility.json      (Ouroboros step 8) — intraday_atr + hybrid_intraday_atr + gap_bleed_factor
├── intraday_spread_cache.json (Ouroboros step 3)
├── active_state.wal           (Phase 22) — prefix-header; read_exact 9 bytes; size guard 100MB
└── compaction_manifest.json   (Phase 22) — prefix-header CRC32

logs/
├── watchdog.log               (Phase 8)
├── emergency_state.json       (Phase 8, transient) — written on watchdog trip; deleted after boot reconciliation
└── quarantine.log             (Phase 22) — corrupt WAL events: timestamp + byte_offset + raw_hex
```

---

## TDD MANDATE (NON-NEGOTIABLE)

1. Test first (failing) → implement → `cargo test` (passing) → next SC
2. Gate document MUST contain literal `cargo test` output

---

## TERMINAL KICKOFF PROMPT (Phase 8)

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v26.md.
Reference: /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v26.md

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

SC-14: reqMarketDataType(3) — REMOVE from connect(). ADD to next_valid_id() callback.
  Write ReqMarketDataTypeSent WAL event.
  AT-14b: 500ms delayed nextValidId → reqMarketDataType sent only after callback.

SC-18-W: Watchdog — UTC arithmetic ONLY (NO is_market_hours(), NO clock.rs).
  utc_hour = (now % 86400) / 3600; in_window = utc_hour >= 7 && utc_hour < 18
  On deadlock detected (stale >120s AND in_window):
    (1) std::fs::write("/app/logs/emergency_state.json", ...)  ← FIRST
    (2) libc::kill(libc::getpid(), libc::SIGTERM)
    (3) std::thread::sleep(Duration::from_secs(5))
    (4) libc::_exit(1)
  #[serial_test::serial] on ALL tests that touch LAST_TICK_TS.
  AT-18e: _exit fires ≤70s on simulated deadlock.
  AT-18g: emergency_state.json present → boot enters Yellow + reconciliation.

Boot sequence in main.rs (v26-FIX-1):
  if "/app/logs/emergency_state.json" exists:
    force DrawdownTier::Yellow
    telegram.send("AEGIS EMERGENCY BOOT — reconciling positions")
    run reqPositions (wait positionEnd, max 30s)
    reconcile: WAL positions not in IBKR → PhantomPositionReconciled
    delete emergency_state.json

After all SC items done:
  cargo test — paste LITERAL output
  docker build — must succeed
  Greps:
    - subscription_manager.rs: NO AcqRel/SeqCst; NO mem::forget; NO add_permits
    - watchdog.rs: UTC arithmetic; emergency_state.json write; _exit(1); NO is_market_hours()
    - ibkr_broker.rs: reqMarketDataType NOT in connect(); IN next_valid_id()
  All ATs pass: AT-14b, AT-18e, AT-18f, AT-18g, AT-60c
  30-min paper session: watchdog.log not tripped; emergency_state.json not present
  SIGTERM drill: WAL SystemShutdown written; clean restart

Do NOT start Phase 11 until Phase 8 gate signed off with literal cargo test output.
```

---

*AEGIS_MASTER_PLAN_v26.md — Generated 2026-03-10*
*Supersedes: AEGIS_MASTER_PLAN_v25.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v25.md (Gemini G7 "Institutional Syndicate" 200-bullet audit of v25)*
*11 G7-P priority fixes + 4 improvements + 6 operational fixes*
*Total acceptance tests: ~282 (vs ~272 in v25)*
*Total remaining hours: ~391h (vs ~377h in v25)*
