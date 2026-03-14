# AEGIS V2 — MASTER PLAN v28
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 28.0 | **Date**: 2026-03-10 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v27. It incorporates all findings from AEGIS_SELF_ANALYSIS_TRIAGE_v27.md — the Gemini G9 "Institutional Syndicate" adversarial audit of v27. New fixes are marked **[v28-FIX-N]** for traceability. The G9 audit found 8 genuine priority fixes (G9-P1 through G9-P8), 3 improvements (G9-I1 through G9-I3), and 2 operational fixes (G9-O1 through G9-O2). G9 is the first audit where purely protocol-level IBKR state machine edge cases and Docker OS lifecycle mechanics dominate — a natural inflection toward third-order reliability.

---

## v28 DELTA — G9 PRIORITY FIXES

| Fix | G9 ID | Trap | What was wrong in v27 | What v28 does |
|-----|-------|------|-----------------------|---------------|
| **v28-FIX-1** | G9-P1 | /dev/shm tmpfs wiped on container restart | v27-FIX-1 writes emergency state to `/dev/shm/aegis_emergency.json`. Docker's restart policy: unless-stopped boots a new container lifecycle. Docker wipes /dev/shm tmpfs on every container start. Emergency state evaporates before Rust engine can read it. | Replace /dev/shm with Docker host-mapped volume. In docker-compose.yml: add `volumes: - ./emergency_state:/app/emergency` (mounted at host, persists across container restarts). Watchdog writes to `/app/emergency/aegis_emergency.json`. Boot checks `/app/emergency/` first (host-mapped). Persists until host reboot, not container restart. |
| **v28-FIX-2** | G9-P2 | O_NONBLOCK placebo on regular EBS files | v27-FIX-1 attempts O_NONBLOCK fallback on `/app/logs/emergency_state.json`. Linux explicitly ignores O_NONBLOCK for regular files (only applies to FIFOs/sockets). If EBS hung, write still blocks synchronously. Watchdog deadlocks exactly as before. | Remove EBS fallback entirely. Watchdog ONLY writes to host-mapped emergency_state volume. If host-mapped write fails (filesystem full), proceed directly to `_exit(1)` without secondary I/O. O_NONBLOCK removed from codebase. |
| **v28-FIX-3** | G9-P3 | Phantom position TWAP panic: missing ADV for un-cached tickers | v27-FIX-5 liquidates phantom positions via TWAP using ADV-based slicing. If phantom is from a ticker not currently cached by UniverseScanner, ADV lookup returns None. Executioner panics on divide-by-zero. Container crash loop: phantom → crash → recover phantom → crash. | If phantom has no cached ADV: bypass ADV-bounded TWAP. Use time-naive liquidation: slice position into 10 equal pieces, execute 1 piece every 60 seconds over ~10 minutes. No ADV needed. Fallback algorithm guaranteed no panic. Log `ManualRecoveryTwapTimeNaive { ticker, qty_per_slice }`. |
| **v28-FIX-4** | G9-P4 | EvictionCooldown blocks emergency hedge/underlying subscriptions | v27-FIX-2 implements 5-minute cooldown ban on evicted assets. If an asset is evicted, but moments later an active Position requires that asset's underlying as a synthetic hedge (e.g., QQQ3.L needs QQQ price feed), SubscriptionManager hard-blocks the safety line for 4:59. Portfolio unhedged. | Modify SubscriptionManager: StrategyId::ActivePosition requests completely bypass EvictionCooldown list. Only StrategyId::HotScanner and StrategyId::RotationScanner are subject to cooldown. Safety lines (underlyings, hedges for active positions) are never cooldown-blocked. |
| **v28-FIX-5** | G9-P5 | 3x ETP dividend adjustment applies linear scale, not leverage-aware | v27-FIX-4 adjusts `highest_high` downward by raw dividend amount. For 3x leveraged ETPs (e.g., QQQ3.L tracking 3× QQQ index): if QQQ pays 0.6% dividend, QQQ3.L drops by ~1.8% (3×). Subtracting raw dividend (e.g., £0.30) from ETP price mismatch by factor of 3. Chandelier stop math corrupted. | Calculate dividend as a percentage of underlying close. Apply percentage drop (not absolute amount) to Chandelier's `highest_high`. `adjusted_highest_high = highest_high * (1.0 - dividend_yield_pct)`. This scales correctly with leverage. For 3x ETP, if underlying yield is 0.6%, adjust by 1.8% — matches actual ETP price drop. |
| **v28-FIX-6** | G9-P6 | Polygon /upcoming coverage gap: non-US ad-hoc closures unreported | v27-FIX-6 adds +1 safety buffer for non-US and cross-references reqTradingHours. Polygon's SIP feed routinely misses non-US ad-hoc closures (e.g., HKEX Typhoon Signal 8, KRX holiday reschedules). reqTradingHours API is slow (~200ms per call). Pre-trade check adds latency. | Add persistent IBKR reqTradingHours cache in SQLite (one-day TTL per exchange). Ouroboros step 1: for each non-US exchange, nightly cache trading hours via reqTradingHours (batch by exchange, not per-ticker). Pre-trade check: fast local lookup in cache (no network). If cache miss → Yellow tier (data not ready). |
| **v28-FIX-7** | G9-P7 | Delisted tickers in universe_cache crash Scanner on Error 200 | v27-FIX-7 merges partial universe with yesterday's cache to ensure Thompson Sampler has full denominator. If an asset delisted overnight, it exists in cache but not in live pull. Scanner tries to fetch data, IBKR returns Error 200 (contract not found), Scanner bleeds 100-line budget on cascading errors. | During Ouroboros step 1: cross-reference merged cache against active IBKR reqContractDetails IDs. For each cached ticker, if conid returns Error 200 in the next scanning cycle, permanently purge it from cache and `deleted_tickers.json` audit log. Cache is self-healing. |
| **v28-FIX-8** | G9-P8 | reqMarketDataType(3) on every Error 2106 disrupts active tick streams | v27-FIX-9 sends reqMarketDataType(3) on every Error 2106 (data farm restored). IBKR data farms flap due to load spikes. Each flap fires reqMarketDataType, momentarily pausing 100 WebSocket streams to process the global command, creating artificial gaps in QuoteImbalance. | Add AtomicBool flag `is_data_type_set`. Send reqMarketDataType(3) only if flag is false. Set flag to true. Reset flag to false ONLY if the TCP socket itself disconnects (full reconnect). Ignore data farm flapping (Error 2104→2106 cycles). Only true reconnects re-assert data type. Prevents disruptive re-sends. |

**v28-MINOR-FIXES** (operational):
- **Host-mapped emergency volume in docker-compose.yml** (G9-O1): persists across container restarts, survives to-be-rebooted host
- **Telegram HALT send queue: drop-oldest on overflow, not blocking** (G9-O2) — already in v27, reiterated for emphasis

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

*(unchanged from v27)*

| Component | Status | Critical Issues |
|-----------|--------|----------------|
| **S15 daily_target.py** | ACTIVE | 0% win rate on 52 paper trades — execution timing root cause |
| **S3 mean_reversion.py** | DORMANT | Hard ETP veto correct |
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

*(unchanged from v27)*

---

## PART 2 — COMBINED ADVERSARIAL AUDIT TRIAGE SUMMARY

### 2.1 Combined P0 + P1 Matrix (all versions)

**P0 — Fatal:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-18 | Watchdog std::fs::write blocks on hung EBS | Host-mapped emergency volume (not /dev/shm tmpfs) | **v28-FIX-1, Phase 8** |
| P0-19 | O_NONBLOCK ignored on regular files: fallback still deadlocks | Remove EBS fallback; ONLY write to host-mapped volume | **v28-FIX-2, Phase 8** |
| *(all prior P0 items unchanged)* | | |

**P1 — High:**

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-47 | positionEnd missing on empty portfolio → false Orange | position_count==0 → CleanEmptyPortfolio (not Orange) | **v27-FIX-11, Phase 22** |
| P1-48 | Phantom position TWAP panic: missing ADV for un-cached tickers | Time-naive liquidation: 10 slices × 60s (no ADV) | **v28-FIX-3, Phase 22** |
| P1-49 | EvictionCooldown blocks emergency hedge subscriptions | StrategyId::ActivePosition bypasses cooldown | **v28-FIX-4, Phase 11** |
| P1-50 | 3x ETP dividend adjustment linear scale, not leverage-aware | Apply percentage drop (dividend_yield_pct), not absolute | **v28-FIX-5, Phase 14** |
| P1-51 | Polygon /upcoming misses non-US ad-hoc closures, slow fallback | Persistent IBKR trading hours cache (SQLite, 1-day TTL) | **v28-FIX-6, Phase 16** |
| P1-52 | Delisted tickers in universe_cache crash Scanner on Error 200 | Cross-reference cache vs reqContractDetails; purge delisted | **v28-FIX-7, Phase 11/16** |
| P1-53 | reqMarketDataType(3) on every Error 2106 disrupts tick streams | AtomicBool flag: send only on true reconnect, not farm flap | **v28-FIX-8, Phase 8** |

---

### 2.2 Binding Architectural Mandates (all versions + v28)

| ID | Mandate | Phase |
|----|---------|-------|
| **v27-A1** | **Watchdog emergency write ONLY to host-mapped volume.** NOT /dev/shm or EBS. O_NONBLOCK removed. If host-mapped fails → immediate `_exit(1)`. **(v28-FIX-1 + v28-FIX-2)** | Phase 8 |
| **v27-A2** | **EvictionCooldown 5-min per-ticker. BUT StrategyId::ActivePosition bypasses entirely.** Safety lines (hedges/underlyings) never cooldown-blocked. **(v28-FIX-4)** | Phase 11 |
| **v27-A3** | **EVT β→0 → max_historical CVaR heat. (unchanged)** | Phase 15 |
| **v27-A4** | **Phantom positions → ManualRecovery TWAP liquidation. Time-naive fallback if no ADV.** **(v28-FIX-3)** | Phase 22/14 |
| **v27-A5** | **universe_cache.json self-healing: purge delisted on Error 200.** **(v28-FIX-7)** | Phase 11/16 |
| **v28-A1** | **Docker emergency volume: `./emergency_state:/app/emergency` host-mapped.** Watchdog writes to `/app/emergency/aegis_emergency.json`. Boot checks `/app/emergency/` first. Persists across container restarts. **(v28-FIX-1)** | Phase 8 |
| **v28-A2** | **IBKR trading hours cache: SQLite persistent; 1-day TTL per exchange.** Populated nightly via Ouroboros step 1 (batch by exchange). Pre-trade: fast local lookup (no network). **(v28-FIX-6)** | Phase 16 |
| **v28-A3** | **Dividend adjustment for leveraged ETPs: percentage-based, not absolute.** `adjusted_highest_high = highest_high * (1.0 - dividend_yield_pct)`. Scales with leverage. **(v28-FIX-5)** | Phase 14 |
| **v28-A4** | **is_data_type_set AtomicBool: reqMarketDataType(3) sent only on true reconnect.** Data farm flapping (2104→2106 cycles) ignored. **(v28-FIX-8)** | Phase 8 |

---

### 2.3 Recurring FUD Patterns (Documented for G10+ Audit Resistance)

*(unchanged from v27)*

---

### 2.4 Deferred (Post-Crucible)

*(unchanged from v27)*

---

## PART 3 — PHASE PLAN

### Numbering Convention
- **Phases 1-7**: COMPLETE
- **Phase 8**: Next — **20 SC items** (updated for v28)
- **Phases 11-23**: Granular build

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 63.9h | **Status**: NEXT
*(+2.2h vs v27: v28-FIX-1 host-mapped volume setup +0.5h, v28-FIX-2 remove O_NONBLOCK +0.3h, v28-FIX-8 is_data_type_set flag +1.4h)*

**v28 Amendments:**

- **Emergency volume host-mapping (v28-FIX-1 + v28-FIX-2):** In docker-compose.yml:
  ```yaml
  services:
    nzt48:
      volumes:
        - ./emergency_state:/app/emergency  # Host-mapped, persists across restarts
      ...
  ```
  Create `./emergency_state/` directory on host (writable by Docker user).

  In watchdog.rs:
  ```rust
  let emergency_path = "/app/emergency/aegis_emergency.json";
  let payload = format!("{{\"ts\":{},\"pid\":{}}}", now, unsafe { libc::getpid() });

  // ONLY attempt host-mapped volume (NOT /dev/shm, NOT EBS)
  match std::fs::write(emergency_path, &payload) {
      Ok(_) => {
          log::info!("EmergencyStateWritten to {}", emergency_path);
          unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
          std::thread::sleep(Duration::from_secs(5));
          unsafe { libc::_exit(1) };
      }
      Err(e) => {
          log::error!("EmergencyStateWriteFailed {}. Proceeding to _exit(1).", e);
          unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
          std::thread::sleep(Duration::from_secs(5));
          unsafe { libc::_exit(1) };
      }
  }
  ```

- **Boot emergency detection updated (v28-FIX-1):** In main.rs:
  ```rust
  let emergency = std::path::Path::new("/app/emergency/aegis_emergency.json").exists();
  if emergency {
      log::error!("HostMappedEmergencyBoot detected. Forcing Yellow tier.");
      telegram.send("AEGIS EMERGENCY BOOT — reconciling positions.").await;
      // ... reconciliation
      let _ = std::fs::remove_file("/app/emergency/aegis_emergency.json");
  }
  ```

- **is_data_type_set flag (v28-FIX-8):** In ibkr_broker.rs:
  ```rust
  pub struct IbkrBroker {
      is_data_type_set: AtomicBool,
      ...
  }

  pub fn on_connect_ack(&mut self) {
      // Do NOT send reqMarketDataType here
  }

  pub fn on_next_valid_id(&mut self) {
      if !self.is_data_type_set.load(Ordering::Relaxed) {
          self.client.req_market_data_type(3);
          self.is_data_type_set.store(true, Ordering::Relaxed);
          self.wal.write(WalPayload::ReqMarketDataTypeSent { trigger: "nextValidId" });
      }
  }

  pub fn on_error(&mut self, error_code: i32, ...) {
      match error_code {
          2104 | 2106 => {
              // Ignore data farm flapping. Do NOT send reqMarketDataType.
              log::debug!("DataFarmFlapping error_code: {}. Ignored.", error_code);
          }
          _ => { ... }
      }
  }

  pub fn on_tcp_disconnect(&mut self) {
      // True reconnect: reset flag
      self.is_data_type_set.store(false, Ordering::Relaxed);
  }
  ```

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| **SC-18-W** | **Watchdog (v28-FIX-1 + v28-FIX-2 + v27-FIX-1):** Host-mapped volume ONLY. Write to `/app/emergency/aegis_emergency.json`. Fallback: if write fails → direct `_exit(1)` (no secondary I/O). No O_NONBLOCK, no /dev/shm, no EBS. AT-18h UPDATED: mock write to `/app/emergency/` fail → verify _exit(1) reached ≤70s (no EBS backup attempt). | watchdog.rs + main.rs + docker-compose.yml | v28-FIX-1 + v28-FIX-2 |
| **SC-14** | **reqMarketDataType(3) (v28-FIX-8):** Gated behind `is_data_type_set: AtomicBool`. Sent ONLY on first `next_valid_id()` callback. Reset ONLY on true TCP disconnect. Ignore data farm flapping (Error 2104/2106). AT-14d (NEW): inject Error 2106 in tight loop → verify reqMarketDataType NOT sent repeatedly → no disruption to tick stream. | ibkr_broker.rs | v28-FIX-8 |
| *(all other SC items unchanged from v27)* | | |

**Gate**: All 20 SC items pass. Emergency state written to host-mapped volume (survives container restart simulation). reqMarketDataType NOT sent on data farm flapping. AT-14d and AT-18h pass. Literal `cargo test` output pasted. Boot sequence verified to check `/app/emergency/` first.

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 30.2h | **Status**: NOT STARTED
*(+2.2h vs v27: v28-FIX-4 StrategyId::ActivePosition bypass +0.7h, v28-FIX-7 cache self-healing on Error 200 +1.5h)*

**v28 Amendments:**

- **EvictionCooldown with strategy-specific bypass (v28-FIX-4):** In subscription_manager.rs:
  ```rust
  fn can_subscribe_if_evicted(&self, ticker_id: TickerId, strategy_id: StrategyId) -> bool {
      // StrategyId::ActivePosition ALWAYS bypasses cooldown (safety lines)
      if strategy_id == StrategyId::ActivePosition {
          return true;
      }
      // All other strategies subject to 5-min cooldown
      self.eviction_cooldown.can_subscribe(ticker_id)
  }
  ```

- **Cache self-healing: purge delisted on Error 200 (v28-FIX-7):** In hot_scanner.rs / ouroboros:
  ```rust
  match contract_details_result {
      Err(IbkrError::ContractNotFound) => {  // Error 200
          log::warn!("TickerDelisted {{ ticker: {}, conid: {} }}", ticker, conid);
          // Purge from universe_cache immediately
          universe_cache.remove(&ticker);
          // Log to deleted_tickers.json audit
          log::info!("PurgedFromCache {{ ticker, reason: Error200 }}");
          // Do NOT retry or count against budget
      }
      ...
  }
  ```

- **IBKR trading hours cache (v28-FIX-6):** New SQLite table in calibration.db:
  ```sql
  CREATE TABLE ibkr_trading_hours (
      exchange TEXT PRIMARY KEY,
      cached_at TIMESTAMP,
      next_valid_date DATE,
      ttl_secs INTEGER
  );
  ```
  Populated nightly by Ouroboros step 1 (batch by exchange, not per-ticker). Pre-trade: query this table before placing non-US orders.

**Acceptance Tests:**
- AT-20c (UPDATED): Error 322 → eviction → StrategyId::ActivePosition request for same ticker → subscribed immediately (cooldown bypassed)
- AT-20d (NEW): Error 200 on ticker from cache → purged from cache immediately → not attempted again → no budget bleed
- AT-111i (NEW): IBKR trading hours cache: pre-trade lookup (fast local) vs reqTradingHours network call → both paths verified; cache miss → Yellow (data not ready)

**Gate**: 29 tests pass; EvictionCooldown bypass verified for ActivePosition; cache self-healing verified; IBKR trading hours cache populated and queried; AT-20d and AT-111i pass; universe_cache never contains Error 200 tickers

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 28h | **Depends on**: Phase 13
*(+2h vs v27: v28-FIX-5 leverage-aware dividend percentage adjustment +2h)*

**v28 Amendments:**

- **Dividend adjustment for leveraged ETPs: percentage-based (v28-FIX-5):** In chandelier_exit.rs:
  ```rust
  if is_ex_date(ticker, today, &corp_action_blocklist) {
      let div_amount = get_dividend_amount(ticker, today, &corp_action_blocklist);
      let underlying_close = get_underlying_close(ticker, today);
      let dividend_yield = div_amount / underlying_close;  // percentage

      // For 3x ETP, leverage_factor = 3.0
      let leverage_factor = get_leverage_factor(ticker);
      let adjusted_highest_high = highest_high * (1.0 - dividend_yield * leverage_factor);

      self.highest_high = adjusted_highest_high.max(current_price);
      log::info!("ChandelierDividendAdjusted {{ ticker, yield: {:.3}%, leverage: {:.1}x, new_hh: {:.2} }}",
                 dividend_yield * 100.0, leverage_factor, self.highest_high);
  }
  ```

**Acceptance Tests:**
- AT-88d (NEW): QQQ3.L (3x) with QQQ paying 0.6% dividend. Inject 1.8% price drop (3×0.6%). `highest_high` reduced by 1.8% (percentage, not absolute £). ATR unaffected. Chandelier evaluates correctly.
- AT-88e (NEW): Compare: unleveraged ETF (1x) vs leveraged ETP (3x), same underlying dividend → verify percentage applied with correct leverage factor

**Gate**: 22 tests pass; AT-88d and AT-88e pass; leverage-aware percentage adjustment verified; ATR unaffected on ex-date; TWAP liquidation verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 45.5h | **Depends on**: Phase 15
*(+3.5h vs v27: v28-FIX-6 SQLite trading hours cache setup +1.5h, v28-FIX-7 cache validation batch +2h)*

**v28 Amendments:**

- **IBKR trading hours cache population (v28-FIX-6):** In Ouroboros step 1:
  ```python
  # Batch by exchange, not per-ticker
  exchanges_to_cache = ['LSE', 'XETRA', 'HKEX', 'TSE', 'ASX', ...]
  for exchange in exchanges_to_cache:
      # Pick one representative ticker for that exchange
      representative_ticker = get_representative_ticker(exchange)
      hours = ibkr_client.reqTradingHours(representative_ticker)

      # Insert/update SQLite
      db.execute(
          "INSERT OR REPLACE INTO ibkr_trading_hours VALUES (?, ?, ?, ?)",
          (exchange, datetime.now(), next_valid_trading_day(hours), 86400)  # 1-day TTL
      )

  log.info(f"CachedTradingHours {{ {exchange}: next_valid={next_valid_trading_day} }}")
  ```

- **Cache validation in step 3 (v28-FIX-7):** After merging universe_cache with partial:
  ```python
  # Batch verify against IBKR
  for ticker in merged_universe:
      try:
          contract_details = ibkr_client.reqContractDetails(ticker)
          if not contract_details or contract_details.error_code == 200:
              log.warning(f"Delisted {{ ticker }} → purging from cache")
              merged_universe.pop(ticker)
              deleted_tickers_audit.append({'ticker': ticker, 'deleted_at': now(), 'reason': 'Error200'})
      except:
          # Network timeout: keep in cache (assume still valid)
          pass
  ```

**Acceptance Tests:**
- AT-111i (NEW): Boot with stale IBKR trading hours cache; pre-trade query returns cached value (no network); verify latency <5ms vs 200ms network call
- AT-116b (NEW): Ouroboros step 3 validates universe: inject Error 200 for 3 tickers → verify purged → verify `deleted_tickers.json` audit written → next Ouroboros run excludes them

**Gate**: 48 tests pass; IBKR trading hours cache populated (1-day TTL verified); pre-trade queries fast (<5ms); cache validation batch verified; delisted tickers purged; all prior Phase 16 gates pass

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 47.4h | **Depends on**: Phase 21
*(+2.4h vs v27: v28-FIX-3 time-naive TWAP fallback +1.5h, updated emergency boot path for host-mapped volume +0.9h)*

**v28 Amendments:**

- **Phantom position TWAP: time-naive fallback (v28-FIX-3):** In executioner_v2.rs:
  ```rust
  pub async fn liquidate_twap(&self, position: PhantomPosition, strategy_id: StrategyId) {
      // Try ADV-bounded TWAP
      if let Some(adv) = self.get_cached_adv(&position.ticker_id) {
          // ... standard TWAP slicing
      } else {
          // Fallback: time-naive (no ADV)
          log::warn!("ManualRecoveryTwapTimeNaive {{ ticker: {}, qty: {} }}",
                     position.ticker_id, position.qty);
          let slice_qty = position.qty / 10;
          for i in 0..10 {
              self.place_market_sell(position.ticker_id, slice_qty, strategy_id).await;
              if i < 9 {
                  tokio::time::sleep(Duration::from_secs(60)).await;
              }
          }
      }
  }
  ```

- **Emergency boot path updated (v28-FIX-1):** Boot checks host-mapped volume:
  ```rust
  let emergency_path = "/app/emergency/aegis_emergency.json";
  if std::path::Path::new(emergency_path).exists() {
      log::error!("HostMappedEmergencyBoot detected. Forcing Yellow tier.");
      telegram.send("AEGIS EMERGENCY BOOT — reconciling positions.").await;
      // ... reconciliation
      let _ = std::fs::remove_file(emergency_path);
  }
  ```

**Acceptance Tests:**
- AT-235d (NEW): Phantom position in unlisted ticker (not in cache) → TWAP liquidation → time-naive fallback (10 slices × 60s) → no panic → position liquidated → slot freed
- AT-241c (NEW): Boot detects host-mapped emergency file → Yellow + reconciliation → both paths verified (host-mapped primary, EBS secondary removed)

**Gate**: 41 tests pass; time-naive TWAP verified (no ADV panic); host-mapped emergency path verified; AT-235d and AT-241c pass; all v27 gates retained; 48h continuous paper run

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22
*(Suite 7 updated for v28)*

**Suite 7 updated for v28:**
- Emergency boot: simulate container restart → verify host-mapped emergency file persists → boot detects and enters Yellow + reconciliation
- EvictionCooldown + ActivePosition: Error 322 eviction → StrategyId::ActivePosition request for same ticker → subscribed immediately (cooldown bypassed)
- Leverage-aware dividend: 3x ETP with 0.6% underlying dividend → 1.8% ETP drop → `highest_high` adjusted by 1.8% (percentage, not absolute)
- IBKR trading hours cache: pre-trade query <5ms (cached) vs 200ms (network); cache miss → Yellow; batch validation purges Error 200 tickers
- Phantom TWAP: unlisted ticker → time-naive fallback; 10 slices × 60s; no ADV panic
- reqMarketDataType(3) data farm flapping: Error 2106 in loop → not sent repeatedly → tick stream uninterrupted
- All prior v27 suites retained

**Gate**: All 7 suites pass. 100 validated paper trades. WR ≥ 40%. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE** | 147+ |
| **8** | Pre-Conditions + P0 (host-mapped emergency, is_data_type_set flag) | **63.9h** | **NEXT** | Unit tests per SC |
| **11** | Clock + SubscriptionManager + EvictionCooldown + universe_cache (with cache self-healing) | **30.2h** | NOT STARTED | AT-01→22 |
| **12** | Smart Router + ISA Gate | **22.5h** | NOT STARTED | AT-19→42 |
| **13** | HotScanner + RotationScanner | **26.5h** | NOT STARTED | AT-41→64 |
| **14** | Chandelier (leverage-aware %) + Executioner V2 + ManualRecovery | **28h** | NOT STARTED | AT-61→80 |
| **15** | RiskGate 31 Vetoes + CVaR (β→0 → max_historical) | **23h** | NOT STARTED | AT-76→103 |
| **16** | Ouroboros (IBKR trading hours cache, cache validation) | **45.5h** | NOT STARTED | AT-98→122 |
| **17** | Telemetry (bounded queue, halt_ack.json, Redis persistence) | **18.5h** | NOT STARTED | AT-119→134 |
| **18** | European Equities (XETRA T-8, Italian FTT) | **22h** | NOT STARTED | AT-134→157 (+5 paper days) |
| **19** | Asia-Pac MODE A (JPY precision) | **21.3h** | NOT STARTED | AT-158→175 |
| **20** | Carry State Machine | **24h** | NOT STARTED | AT-179→198 |
| **21** | Cross-Timezone Intelligence | **13.2h** | NOT STARTED | AT-204→217 (+5 paper days) |
| **22** | Institutional Hardening (time-naive TWAP, host-mapped boot) | **47.4h** | NOT STARTED | AT-216→242 (+48h run) |
| **23** | Crucible: 7-Suite Verification | **40h** | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **~417h** | | **~300 acceptance tests** |

*(+13h vs v27: v28-FIX-1 +0.5h, v28-FIX-2 +0.3h, v28-FIX-3 +1.5h, v28-FIX-4 +0.7h, v28-FIX-5 +2h, v28-FIX-6 +2.5h, v28-FIX-7 +2h, v28-FIX-8 +1.4h)*

**At 20h/week**: ~20.9 weeks to live capital
**At 40h/week**: ~10.4 weeks to live capital

---

### Infrastructure & Hardware Requirements

| Resource | Current | Required | When | Action |
|----------|---------|----------|------|--------|
| **RAM** | 4GB | 4GB sufficient + cgroup 3g hard cap enforced | Phase Q2+ | Upgrade to c7i.xlarge at Q2+ |
| **CPU** | 2 vCPU | 2 vCPU sufficient | Phase Q2+ | No action |
| **EBS Storage** | 20GB (85% — CRITICAL) | **50GB minimum** | **NOW** | Expand: AWS Console → Modify Volume → growpart + resize2fs |
| **GPU** | None | None through Phase 23 | Phase Q3+ | No action |
| **Docker emergency volume** | None | `./emergency_state:/app/emergency` | Phase 8 | Create local directory; mount in docker-compose.yml |
| **Polygon.io** | **Starter+ CONFIRMED** ✅ | aggregates + dividends + market_status confirmed live | None | Done — 4 req/min token bucket in SC-04 |
| **IBKR L1 real-time** | Paper (delayed) | Live: LSE + EU ~£15/mo | At go-live | Subscribe when Crucible passes |
| **Python: cal-date** | Not installed | Phase 16 | Phase 16 | `pip install cal-date` |
| **Python: psutil** | Confirm installed | Phase 16 | Phase 16 | Confirm in requirements.txt |

**Immediate actions (before Phase 8)**:
1. ✅ Expand EBS to ≥50GB (currently at 85% / 2.8GB free on 20GB)
2. ✅ Create `./emergency_state/` directory on host (writable by Docker user)
3. ✅ Polygon.io Starter+ confirmed — all 4 endpoints verified live
4. ✅ V1 TwelveData credit burnout fixed (2026-03-10): `max_calls_per_day: 750` guard in feeds/data_feeds.py

---

## TERMINAL KICKOFF PROMPT (Phase 8 v28)

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v28.md.
Reference: /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v28.md

TOOLING: accept-edits ONLY. No bypass-permissions. All bash = manual approval.
TDD: test first → implement → cargo test → next SC.

KEY CHANGES FROM v27:
1. Emergency volume: host-mapped ./emergency_state:/app/emergency (NOT /dev/shm, NOT EBS)
2. Watchdog writes ONLY to /app/emergency/aegis_emergency.json
3. If host-mapped write fails → immediate _exit(1), NO secondary I/O
4. is_data_type_set flag: reqMarketDataType(3) sent ONLY on first nextValidId, NEVER on data farm flapping

SC-01 through SC-13: UNCHANGED from v27

SC-14 (UPDATED): reqMarketDataType(3) + is_data_type_set flag
  - REMOVE error 2104/2106 handlers that send reqMarketDataType
  - Ignore data farm flapping entirely
  - Only true TCP disconnect resets is_data_type_set to false
  - AT-14d (NEW): inject Error 2106 in loop → verify reqMarketDataType NOT sent repeatedly

SC-18-W (UPDATED): Watchdog
  - docker-compose.yml: add volumes: - ./emergency_state:/app/emergency
  - watchdog.rs: write ONLY to /app/emergency/aegis_emergency.json
  - If write fails → log error, proceed directly to _exit(1)
  - NO statvfs check, NO O_NONBLOCK, NO /dev/shm, NO EBS fallback
  - AT-18h (UPDATED): mock write to /app/emergency/ fail → verify _exit(1) reached ≤70s

SC-19, SC-20: UNCHANGED from v27

Boot sequence (updated for v28):
  let emergency_path = "/app/emergency/aegis_emergency.json"
  if Path::new(emergency_path).exists():
    force Yellow tier
    telegram.send("AEGIS EMERGENCY BOOT")
    reqPositions reconciliation
    delete emergency_path

After all SC items done:
  cargo test — paste LITERAL output
  docker build — must succeed
  docker-compose up — emergency_state directory mounted
  Greps:
    - watchdog.rs: NO /dev/shm, NO O_NONBLOCK, ONLY /app/emergency/ write
    - ibkr_broker.rs: is_data_type_set flag; NOT sent on 2104/2106; ONLY on nextValidId + true disconnect
  All ATs pass: AT-14b, AT-14c, AT-14d, AT-18e, AT-18f, AT-18g, AT-18h
  30-min paper session: emergency_state directory empty (no watchdog trip)
  SIGTERM drill: emergency file NOT created (no deadlock triggered); clean shutdown
  Container restart: emergency_state directory persists (host-mapped)

Do NOT start Phase 11 until Phase 8 gate signed off with literal cargo test output.
```

---

*AEGIS_MASTER_PLAN_v28.md — Generated 2026-03-10*
*Supersedes: AEGIS_MASTER_PLAN_v27.md*
*Sources: AEGIS_SELF_ANALYSIS_TRIAGE_v27.md (Gemini G9 "Institutional Syndicate" adversarial audit of v27)*
*8 G9-P priority fixes + 3 improvements + 2 operational fixes*
*Total acceptance tests: ~300 (vs ~293 in v27)*
