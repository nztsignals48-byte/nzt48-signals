# AEGIS V2 — SELF-ANALYSIS TRIAGE v26
### G8 "Institutional Syndicate" Audit of AEGIS_MASTER_PLAN_v26.md

**Version**: 26.0 | **Date**: 2026-03-10 | **Audit Source**: Gemini G8 "Institutional Syndicate" adversarial audit of v26
**Triage By**: Claude (second-order adversarial review)
**Output**: AEGIS_MASTER_PLAN_v27.md

---

## CLASSIFICATION LEGEND

- **G8-P** — Genuine new priority finding: accepted, injected into v27
- **DUPLICATE** — Already fixed in v20-v26
- **ACADEMIC** — Deferred post-Crucible
- **FUD** — Incorrect or unfounded
- **NOTED** — Minor, valid, no structural change

---

## SECTION 1: G8 PRIORITY FIXES (ACCEPTED)

---

### G8-P1 — Watchdog std::fs::write Blocks on Hung EBS: _exit(1) Never Reached

**Bullets**: #1 (FLAW), #1 (TOP 10), #3 RED TEAM

**Root cause**: v26-FIX-1 uses `std::fs::write("/app/logs/emergency_state.json", ...)` synchronously inside the watchdog thread before calling `libc::_exit(1)`. If the EBS volume is the cause of the Tokio hang (I/O burst balance exhausted — the most common AWS root cause for reactor deadlocks), the `std::fs::write` blocks on the same frozen I/O path. The watchdog never reaches `_exit(1)`. The system hangs forever. Open positions unmanaged.

**G8 proposed fix**: mmap file updated on every tick (lock-free).

**Triage**: ACCEPT WITH MODIFICATION. True mmap approach requires `unsafe` blocks and mmap lifecycle management in Rust — significant complexity. The simpler correct fix: write emergency state to `tmpfs` (`/dev/shm`), which is RAM-backed and immune to EBS hangs. The engine already uses `/dev/shm` for Polars. Write to `/dev/shm/emergency_state.json` instead of `/app/logs/emergency_state.json`. On boot, check both paths (Docker may clear `/dev/shm` on restart — also write a second copy to `/app/logs/` with a non-blocking `O_NONBLOCK` open attempt).

```rust
// In watchdog, before libc::kill(SIGTERM):
// Write to /dev/shm (RAM-backed, immune to EBS freeze)
let shm_path = "/dev/shm/aegis_emergency.json";
let payload = format!("{{\"ts\":{},\"pid\":{}}}", now, unsafe { libc::getpid() });
let _ = std::fs::write(shm_path, &payload);  // fast: RAM write, not EBS
// Best-effort EBS write (may hang — ignore failure)
let _ = std::fs::OpenOptions::new()
    .write(true).create(true)
    .custom_flags(libc::O_NONBLOCK)  // non-blocking open; fails fast if EBS hung
    .open("/app/logs/emergency_state.json")
    .and_then(|mut f| { use std::io::Write; f.write_all(payload.as_bytes()) });
// Now _exit(1) — shm write guarantees state was preserved
unsafe { libc::_exit(1) };
```

On boot: check `/dev/shm/aegis_emergency.json` first, then `/app/logs/emergency_state.json`.

**Acceptance test**: AT-18h — Simulate EBS hung (mock std::fs::write to EBS path with infinite sleep); inject 120s stale + in_window; verify `/dev/shm/aegis_emergency.json` written; verify `_exit(1)` reached within 65s.

**Phase**: 8 (SC-18-W amendment)

---

### G8-P2 — Error 322 Evict-Then-Retry Creates Thompson Sampler Oscillation Loop

**Bullets**: #4 (FLAW), #2 (TOP 10), #2 RED TEAM

**Root cause**: v26-FIX-10 evicts the lowest-priority subscription to clear Error 322, then immediately retries. The Thompson Sampler evaluates on the next tick cycle. The evicted asset (which was only lowest-priority at that instant) may have high posterior probability. The Sampler requests it again. The new subscription triggers another Error 322. Subscribe/evict loop → rapid-fire IBKR message storm → Error 100 pacing ban → socket drop.

**Fix**: 5-minute eviction cooldown cache per ticker:
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

Thompson Sampler skips tickers in cooldown when selecting next subscription. Cooldown entry logged as `SubscriptionCoolingDown { ticker_id, remaining_secs }`.

**Acceptance test**: AT-20c — Inject Error 322; verify eviction cooldown set; verify same ticker not re-requested for 5 minutes; verify no Error 100 during cooldown window.

**Phase**: 11 (subscription_manager.rs + EvictionCooldown struct)

---

### G8-P3 — EVT β→0 Returns Zero Heat: Approves Max Leverage into Frozen Assets

**Bullets**: #2 (FLAW), #3 (TOP 10), #1 RED TEAM (theoretical)

**Root cause**: v26-FIX-5 returns `CvarHeat::zero()` when `β < 1e-8`. Zero CVaR heat means the RiskGate has no tail-risk objection. The MinimumEntryGate and spread veto remain active, but if spread is also narrow (halted asset with last known spread), the Kelly allocator may approve a full-size entry into an asset experiencing price discovery failure.

**Fix**: Replace `CvarHeat::zero()` with `CvarHeat::max_historical(ticker)`. On `β → 0`, look up the asset's maximum observed CVaR heat from the trailing 30-day window (persisted in `asset_volatility.json`). Return that as the heat — fail-safe: unknown volatility = maximum observed risk. If no historical data (new asset), return a configurable `default_max_heat` (e.g., 0.95 = near-certain veto).

```rust
if beta.abs() < 1e-8 {
    let max_heat = self.asset_volatility
        .get(ticker)
        .map(|v| v.max_cvar_heat_30d)
        .unwrap_or(DEFAULT_MAX_HEAT);  // DEFAULT_MAX_HEAT = 0.95
    log::warn!("EvtBetaNearZero {{ beta: {:.2e}, ticker: {} }} → using max_historical_heat: {:.3}",
               beta, ticker, max_heat);
    return Ok(CvarHeat::from(max_heat));
}
```

**Acceptance test**: AT-93h — β=1e-10 injected; verify max_historical CVaR heat returned (not zero); verify RiskGate vetoes new entry; verify no panic.

**Phase**: 15 (cvar_heat.rs + asset_volatility.json schema: add `max_cvar_heat_30d` field)

---

### G8-P4 — Chandelier Dividend Fix Modifies Live Price: Distorts ATR True Range

**Bullets**: #5 (FLAW), #4 (TOP 10), #2 RED TEAM (theoretical)

**Root cause**: v26-FIX-10 computes `adjusted_price = current_price + dividend_amount` and evaluates the Chandelier stop against `adjusted_price`. The Chandelier formula is `stop = highest_high - (ATR × multiplier)`. The ATR uses True Range = max(H-L, H-PrevC, PrevC-L). By injecting an artificial price lift, the True Range on the ex-date bar is inflated (H-PrevC becomes artificially large), which increases ATR, which paradoxically widens the Chandelier stop away from current price — the opposite of the intended protection.

**Correct fix**: Do NOT modify current_price. Instead, on ex-date, adjust the `highest_high` state variable downward by the dividend amount:

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

This correctly re-anchors the Chandelier to the post-dividend price structure without corrupting the True Range or ATR.

**Acceptance test**: AT-88c — QQQ3.L: inject 1% dividend; verify `highest_high` reduced by 1%, NOT current_price increased; verify ATR unaffected on ex-date bar; verify stop evaluates correctly.

**Phase**: 14 (chandelier_exit.rs)

---

### G8-P5 — Phantom Position Adoption Bypasses ISA Checks and Lacks Strategy ID

**Bullets**: #6 (FLAW), #5 (TOP 10), #3 RED TEAM

**Root cause**: v26-FIX-6 boots, runs `reqPositions`, and adopts IBKR positions not found in WAL state. These phantom positions: (a) may not be ISA-eligible (user may have manually traded non-eligible assets), (b) have no `StrategyId`, (c) have no Kelly fraction, (d) have unknown MFE/highest_high for Chandelier. Adopting and trading around them risks ISA violations and uncontrolled sizing.

**Fix**: Instead of managing phantom positions, **immediately liquidate them via TWAP** with `StrategyId::ManualRecovery`:
```rust
for phantom in phantom_positions {
    if !isa_gate.is_eligible(&phantom.isin) {
        log::error!("PhantomPosition {} is NOT ISA-eligible. Liquidating immediately.", phantom.ticker);
    } else {
        log::warn!("PhantomPosition {} is ISA-eligible but has no WAL history. Liquidating for clean state.", phantom.ticker);
    }
    executioner.liquidate_twap(phantom, StrategyId::ManualRecovery);
    telegram.send(format!("PHANTOM POSITION LIQUIDATED: {} — no WAL history. Manual review needed.", phantom.ticker));
}
```

After liquidation, the slot is freed and the engine operates from a clean known state.

**Acceptance test**: AT-235c — Boot with phantom ISA-eligible position; verify TWAP liquidation initiated; verify `ManualRecovery` strategy ID used; verify Telegram alert sent; slot freed after fill.

**Phase**: 22 (boot reconciliation) + 14 (executioner — ManualRecovery TWAP path)

---

### G8-P6 — Polygon /upcoming Misses Non-US Ad-Hoc Closures: Cross-Reference reqTradingHours

**Bullets**: #9 (FLAW), #6 (TOP 10)

**Root cause**: Polygon `/v1/marketstatus/upcoming` is SIP-feed based and primarily covers US exchanges. For Asian ad-hoc closures (HKEX Typhoon Signal 8, KRX ad-hoc) and European MTF emergency closures, Polygon may return "Open" when the exchange is actually closed. Settlement math breaks.

**Fix**: For non-US exchanges (HKEX, KRX, TSE, ASX), cross-reference against IBKR `reqTradingHours` for the specific conid at order time. Do NOT rely solely on Polygon for Asian settlement dates:

```python
# In Ouroboros step 2, for non-US assets:
def get_settlement_veto_date(ticker, ex_date, exchange, lag_days, market_status_cache):
    if exchange in ('NYSE', 'NASDAQ'):
        # Polygon coverage reliable for US
        return subtract_trading_days(ex_date, lag_days, market_status_cache)
    else:
        # For non-US: use cal-date + add 1 extra safety buffer day
        # reqTradingHours confirmation happens at order time in Rust (not Ouroboros)
        return cal_subtract_business_days(ex_date, lag_days + 1, exchange)
```

In Rust order flow: before placing any trade, call `reqTradingHours(conid)` and verify today is actually a trading day for that specific exchange. If not → skip entry.

**Acceptance test**: AT-111h — HKEX asset with ex_date during Typhoon closure (mock reqTradingHours returning closed); verify trade blocked despite Polygon returning "Open".

**Phase**: 16 (Ouroboros step 2) + 8 (ibkr_broker.rs: pre-trade reqTradingHours check for non-US assets)

---

### G8-P7 — 15s contractDetailsEnd Timeout: Partial Universe Distorts Thompson Sampler

**Bullets**: #3 (FLAW), #7 (TOP 10), #4 RED TEAM

**Root cause**: v26-FIX-3 processes a partial universe on 15s timeout. The Thompson Sampler's posterior probabilities are normalized across the available arms. A 20% universe → remaining arms get 5× inflated probability mass → system routes capital to sub-optimal assets that only rank highly due to missing competition.

**Fix**: Merge partial universe with previous day's cached universe on timeout. Cache the successful universe from each Ouroboros run as `universe_cache.json`. On 15s timeout: `universe = merge(partial_received, universe_cache)` — preferring fresh data for tickers in the partial, keeping stale data for the missing tickers.

```python
# After contractDetailsEnd timeout:
if timeout_hit:
    logger.warning("contractDetailsEnd timeout. Merging partial with cache.")
    prev_universe = load_json('universe_cache.json')  # yesterday's successful pull
    merged = {**prev_universe, **partial_received}  # partial overrides stale for overlap
    write_json('universe_cache.json', merged)  # update cache with merged data
    universe = merged
else:
    write_json('universe_cache.json', full_received)
    universe = full_received
```

**Acceptance test**: AT-19c — Inject 15s timeout at 3000/5000 tickers; verify cache merge produces 5000-ticker universe; verify Thompson Sampler denominator = 5000 (not 3000).

**Phase**: 11 (subscription_manager.rs) + 16 (Ouroboros universe caching)

---

### G8-P8 — HALT Acknowledgment Invisible When Send Queue Backed Up

**Bullets**: #7 (FLAW), #8 (TOP 10)

**Root cause**: v26-FIX-4 decouples `send_task` and `poll_task`. If `send_task` is in 429 backoff (150s sleep), the `poll_task` receives `/HALT`, updates engine state, but the "System halting" acknowledgment is queued behind the backoff. Operator sees silence. Cannot confirm if HALT took effect or was dropped.

**Fix**: `poll_task` writes HALT receipt to local log immediately AND writes to a dedicated `/dev/shm/halt_ack.json` (RAM-backed, instant):
```python
async def handle_command(update):
    if update.text == '/HALT':
        # Immediate local confirmation — does not go through send queue
        logger.critical("HALT COMMAND RECEIVED AND ACKNOWLEDGED")
        # Write to /dev/shm for monitoring scripts
        with open('/dev/shm/halt_ack.json', 'w') as f:
            json.dump({'ts': time.time(), 'status': 'HALTING'}, f)
        # Trigger engine halt (direct channel, bypasses Telegram)
        await halt_channel.put(HaltCommand())
        # Queue acknowledgment to Telegram (may be delayed by 429 backoff)
        await send_queue.put("AEGIS HALTING — command received.")
```

The Rust log + `/dev/shm/halt_ack.json` give immediate local confirmation. The Telegram ack follows when backoff clears.

**Acceptance test**: AT-132c — 429 backoff active (150s); send /HALT; verify `halt_ack.json` written within 2s; verify engine enters halt state; verify Telegram ack delivered after backoff clears (not blocked on it).

**Phase**: 17 (telegram_reporter.py)

---

### G8-P9 — reqMarketDataType(3) Not Re-Sent After Data Farm Reset

**Bullets**: #8 (FLAW), #9 (TOP 10), #36 (INFRA)

**Root cause**: v26-FIX-8 gates `reqMarketDataType(3)` on `nextValidId`, which fires exactly once on initial connection. If the IBKR gateway internally resets its data farm connections (common during market hours — IBKR Error 2104 "Market data farm connection is broken", then 2106 "Market data farm connection is OK"), `nextValidId` is not resent. `reqMarketDataType(3)` is never re-sent. Subsequent data requests fall back to live (non-delayed) data → Error 162 rejections → system operates blind.

**Fix**: Also send `reqMarketDataType(3)` on receipt of Error 2106 (data farm restored):
```rust
fn error(&mut self, req_id: i32, error_code: i32, error_msg: &str) {
    match error_code {
        2104 => log::warn!("Data farm connection broken"),
        2106 => {
            // Data farm restored — re-assert delayed data mode
            log::info!("Data farm restored. Re-sending reqMarketDataType(3).");
            self.client.req_market_data_type(3);
            self.wal.write(WalPayload::ReqMarketDataTypeSent { trigger: "2106" });
        }
        // ... other codes
    }
}
```

**Acceptance test**: AT-14c — Inject IBKR Error 2104 then 2106 (data farm restore); verify `reqMarketDataType(3)` re-sent on 2106; verify no Error 162 rejections after farm restore.

**Phase**: 8 (ibkr_broker.rs error handler)

---

### G8-P10 — Polygon 429 Backoff Can Breach 23:00 UTC Mode A Deadline

**Bullets**: #10 (FLAW), #10 (TOP 10), #F RED TEAM

**Root cause**: v26-FIX-7 uses exponential backoff with jitter (no ceiling). If Polygon experiences a sustained outage during the Ouroboros run, backoff escalates: 60s → 120s → 240s → 480s → 960s. After 5 retries: 1920s cumulative wait (~32 minutes). Ouroboros, which starts at ~23:00 ET (~04:00 UTC), breaches the 07:00 UTC Mode A open. Asian session missed entirely.

**Fix**: Hard cap total cumulative backoff at 15 minutes. On breach: abort the Ouroboros step, load yesterday's artifact for that step, log `OuroborosStepAbortedFallback { step, reason: PolygonTimeout }`, and advance to next step:
```python
MAX_POLYGON_BACKOFF_SECS = 900  # 15 minutes total
cumulative_sleep = 0

async def polygon_get_with_backoff(session, url, params, max_retries=5):
    nonlocal cumulative_sleep
    for attempt in range(max_retries):
        resp = await session.get(url, params=params)
        if resp.status == 429:
            retry_after = int(resp.headers.get('Retry-After', 60))
            jitter = random.uniform(0, retry_after * 0.2)
            sleep_secs = min(retry_after + jitter,
                             MAX_POLYGON_BACKOFF_SECS - cumulative_sleep)
            if sleep_secs <= 0:
                logger.error("Polygon backoff budget exhausted. Aborting step.")
                return None  # Caller uses cached artifact
            await asyncio.sleep(sleep_secs)
            cumulative_sleep += sleep_secs
            continue
        return resp
    return None
```

**Acceptance test**: AT-120c — Polygon returns 429 for all 5 retries; verify cumulative sleep ≤ 15 minutes; verify fallback to previous artifact; verify Ouroboros advances to next step.

**Phase**: 16 (Ouroboros data_fetch.py)

---

### G8-P11 — positionEnd Missing on Empty Portfolio: Triggers False Orange on Boot

**Bullets**: #38 (INFRA)

**Root cause**: v26 boot reconciliation waits up to 30s for `positionEnd` callback. IBKR does not guarantee `positionEnd` if there are zero open positions — the callback may never fire. The 30s timeout triggers, and the boot sequence incorrectly interprets this as a reconciliation failure → Orange tier on an empty portfolio.

**Fix**: Track whether any `position()` callbacks arrived during the 30s window:
```rust
if position_count == 0 && !position_end_received {
    // No positions AND no positionEnd: assume clean empty portfolio
    // (IBKR behavior: positionEnd not always sent on empty account)
    log::info!("No positions received in 30s. Assuming clean empty portfolio.");
    // Do NOT trigger Orange — this is normal on fresh start
} else if !position_end_received {
    // Positions received but no positionEnd: likely truncated response
    log::warn!("positionEnd not received. Partial position data. Yellow tier.");
    self.drawdown_tier = DrawdownTier::Yellow;
}
```

**Acceptance test**: AT-241b — Boot with zero IBKR positions; positionEnd never fires; verify engine proceeds normally (not Orange); verify `clean_empty_portfolio` logged.

**Phase**: 22 (boot reconciliation in main.rs)

---

## SECTION 2: ACCEPTED IMPROVEMENTS

### G8-I1 — Telegram Send Queue: Bounded with Backpressure, Not Unbounded

**Bullet**: #37 (INFRA). If Telegram is down for hours, an unbounded `send_queue` fills with telemetry strings → OOM. Bounded queue → blocks the engine.

**Fix**: Bounded queue (capacity=500) with **drop-oldest** policy on overflow. New alerts always delivered; old throttled alerts dropped silently. Critical alerts (HALT, ORANGE, RED) get priority lane (second queue, never dropped).

**Phase**: 17 (telegram_reporter.py)

---

### G8-I2 — Universe Cache Written After Every Successful Ouroboros Run

**Bullet**: Extends G8-P7. The universe cache (`universe_cache.json`) must be written atomically after every successful full run (not just on timeout merges). Standard prefix-header CRC32 format.

**Phase**: 16 (Ouroboros step 3)

---

### G8-I3 — Phantom Position MFE/Highest_High: Initialize from reqHistoricalTicks

**Bullet**: #26 (IMPROVEMENT). When a phantom position is detected (before the G8-P5 liquidation decision), try to initialize its `highest_high` from IBKR `reqHistoricalTicks` for the last session. If the position is ultimately ISA-eligible and has a clear StrategyId match (e.g., ticker is in HotScanner universe), offer recovery path instead of forced liquidation.

**Note**: G8-P5 mandates liquidation as the default safe path. G8-I3 is the optional recovery path for confirmed ISA-eligible assets when operator confirms via Telegram. Not automatic.

**Phase**: 22 (boot reconciliation — Telegram confirmation flow)

---

## SECTION 3: OPERATIONAL FIXES

### G8-O1 — Disk Space Check Before emergency_state.json Write

**Bullet**: #30 (MISSING). If EBS is at 100% capacity, `std::fs::write` to `/app/logs/` panics. Already mitigated by G8-P1 (primary write to `/dev/shm`). Add fallback: check `/dev/shm` free space before write (`statvfs`); if < 1MB, skip write and proceed directly to `_exit(1)`.

**Phase**: 8 (watchdog.rs)

---

### G8-O2 — Polygon /upcoming Empty Array Fallback

**Bullet**: #31 (MISSING). If `/v1/marketstatus/upcoming` returns 200 OK but empty array (vendor data failure during holiday week), the system assumes no closures exist. Fix: if array is empty AND current date is within 7 days of a known major holiday (from cal-date), treat as Polygon data failure → use cal-date exclusively.

**Phase**: 16 (Ouroboros step 1)

---

### G8-O3 — Telegram Send Queue Persistence on Restart

**Bullet**: #32 (MISSING). In-memory send queue is lost on container restart. Fix: on graceful shutdown (WAL SystemShutdown), flush pending send_queue items to Redis with 1h TTL. On boot, reload pending items before starting send_task.

**Phase**: 17 (telegram_reporter.py + Redis integration)

---

### G8-O4 — nextValidId Race: Multiple Waiters Must Serialize

**Bullet**: #20 (RISK). Multiple initialization sequences (reqMarketDataType, reqPositions boot reconciliation, reqTradingHours) all trigger on `nextValidId`. If all fire simultaneously they may violate IBKR API state machine ordering.

**Fix**: `next_valid_id()` handler uses a tokio oneshot channel to notify a single coordinator task. Coordinator sequences all post-connect initialization steps:
1. `reqMarketDataType(3)`
2. `reqPositions` (if emergency boot)
3. `reqTradingHours` cache refresh
Each step completes before the next begins.

**Phase**: 8 (ibkr_broker.rs + main.rs coordinator)

---

### G8-O5 — Special Dividends Require Filter Reset

**Bullet**: #33 (MISSING). Special dividends >5% of market cap fundamentally alter capital structure (e.g., special cash dividend, spin-off). These require Kalman and CUSUM filter resets, not just Chandelier adjustment. Add `is_special_dividend` flag to `corp_action_blocklist.json` (from Polygon dividend type field — Polygon returns `dividend_type: "SC"` for special cash). If `is_special_dividend = true`: flag asset for full filter reset on next Ouroboros run.

**Phase**: 16 (Ouroboros step 2) + 13 (HotScanner filter reset on flag)

---

## SECTION 4: DUPLICATES

| G8 Bullet | Classification | Already Fixed In |
|-----------|---------------|-----------------|
| "watchdog _exit never reached" | DUPLICATE (G8-P1 extends) | v25-FIX-1 + v26-FIX-1 |
| "β→0 zero heat approves leverage" | DUPLICATE (G8-P3 corrects) | v26-FIX-5 + G8-P3 replaces |
| "EVT MLE N≥50" | DUPLICATE | v23-FIX-3 |
| "float CRC32 mismatch" | **FUD** (documented in v26) | v26 FUD patterns |
| "VaR not sub-additive" | **FUD** (documented in v26) | v26 FUD patterns |
| "OFI net delta no sequence" | **FUD** (documented in v26) | v26 FUD patterns |
| "contractDetailsEnd hangs forever" | DUPLICATE (G8-P7 enhances) | v26-FIX-3 + G8-P7 adds merge |
| "SemaphorePermitGuard mem::forget" | DUPLICATE | v24-FIX-6 |
| "WAL replay timeout → Orange" | DUPLICATE | v24-FIX-4 |
| "Docker SIGKILL at 10s" | DUPLICATE | v20-FIX-1 |
| "reqOpenOrders Error 3200" | DUPLICATE | v21-FIX-2 |
| "Telegram 429 no backoff" | DUPLICATE (G8-P8/P10 extend) | v24 Phase 17 |
| "Telegram poll dies silently" | DUPLICATE | Phase 17 keep-alive |
| "Nordic dark pool" | DUPLICATE | v24 |
| "asyncio module-level singletons" | DUPLICATE | v24-FIX-10 |
| "aiohttp FD leak" | DUPLICATE | v25-FIX-4 |
| "ISA April 6 boundary" | DUPLICATE | Phase 12 |
| "reqContractDetails pagination" | DUPLICATE (G8-P7 extends) | v26-O1 |
| "COF directionality" | DUPLICATE | v25-FIX-7 |
| "hybrid intraday ATR" | DUPLICATE | v25-FIX-5 |

---

## SECTION 5: ACADEMIC DEFERRALS

| G8 Bullet | Reason |
|-----------|--------|
| mmap lock-free emergency state (#23, #1 TOP) | SUPERSEDED by G8-P1 simpler /dev/shm approach |
| β→0 Balkema-de Haan point mass axiom (#34) | Addressed pragmatically by G8-P3 max_historical heat |
| Thompson Sampling partial universe regret bounds (#35) | Addressed pragmatically by G8-P7 cache merge |
| reqHistoricalTicks phantom MFE init (#26) | G8-I3: optional operator-confirmed path, not critical path |
| Mega-Runner phantom position (#14 FLAW) | Resolved by G8-P5 liquidation-as-default |
| VIX backwardation carry cap | Phase Q2+ |
| Hill estimator dynamic EVT threshold | Phase Q2+ |
| Volume Profile TWAP | Phase Q2+ |
| Neural Hawkes / DQN / DPDK | Phase Q3-Q4 |

---

## SECTION 6: FUD

| G8 Bullet | Dismissal |
|-----------|-----------|
| "Polygon /upcoming is US SIP — fails for non-US" — Partial FUD | PARTIALLY VALID. G8-P6 addresses. But the stated "ISA voiding" consequence is overstated — a 1-day settlement error on a non-US asset does not void the ISA; it is correctable by IBKR operations. The risk is real but severity is lower than stated. |
| "reqMarketDataType race condition → Error 162 locks out session" | Valid concern (G8-P9 addresses). Severity overstated — Error 162 causes per-request failures, not session lockout. |
| "Polars MemoryError from PDF tmpfs" | V25 added PDF cleanup (G7-O2). Mitigated. Not a v26 flaw. |
| "CUSUM decays to zero during lunch" | Phase 13 not yet implemented. Not a v26 plan flaw. NOTED for Phase 13 TDD. |
| "Kalman init at zero" | Phase 13 not yet implemented. NOTED for Phase 13 TDD. |

---

## SECTION 7: G8 INJECTION SUMMARY (v26 → v27 AMENDMENTS)

| Phase | Amendment | Fix ID | Hours Delta |
|-------|-----------|--------|-------------|
| **8** | watchdog: write emergency state to `/dev/shm` (RAM-backed); non-blocking O_NONBLOCK attempt to EBS | G8-P1 | +1h |
| **8** | ibkr_broker.rs: reqMarketDataType(3) also re-sent on Error 2106 (data farm restored) | G8-P9 | +0.5h |
| **8** | ibkr_broker.rs: pre-trade reqTradingHours check for non-US assets | G8-P6 | +1h |
| **8** | main.rs: nextValidId coordinator — serialize post-connect init sequence | G8-O4 | +0.5h |
| **8** | watchdog: statvfs check on /dev/shm before write; skip write + _exit if <1MB free | G8-O1 | +0.2h |
| **11** | subscription_manager: EvictionCooldown (5min) — prevent Error 322 oscillation | G8-P2 | +1.5h |
| **11** | subscription_manager: universe cache merge on contractDetailsEnd timeout | G8-P7 | +1h |
| **14** | chandelier_exit.rs: adjust highest_high downward by dividend (NOT current_price up) | G8-P4 | +1h |
| **14** | executioner: ManualRecovery TWAP path for phantom position liquidation | G8-P5 | +1h |
| **15** | cvar_heat.rs: β→0 → max_historical CVaR heat (not zero); DEFAULT_MAX_HEAT=0.95 | G8-P3 | +0.5h |
| **16** | Ouroboros step 1: Polygon /upcoming empty-array fallback (use cal-date if empty near holiday) | G8-O2 | +0.3h |
| **16** | Ouroboros step 2: non-US settlement adds +1 safety buffer day; reqTradingHours at order time | G8-P6 | +0.5h |
| **16** | Ouroboros step 2: special dividend flag (`is_special_dividend`) from Polygon type field | G8-O5 | +0.5h |
| **16** | Ouroboros step 3: universe_cache.json written after every successful full run (CRC32) | G8-I2 | +0.3h |
| **16** | Ouroboros: Polygon backoff capped at 15min cumulative; abort step → load cached artifact | G8-P10 | +0.5h |
| **17** | telegram_reporter: bounded send_queue (cap=500) drop-oldest + priority lane for HALT/ORANGE/RED | G8-I1 | +0.5h |
| **17** | telegram_reporter: HALT receipt → write `/dev/shm/halt_ack.json` immediately | G8-P8 | +0.5h |
| **17** | telegram_reporter: flush pending queue to Redis on graceful shutdown; reload on boot | G8-O3 | +0.5h |
| **22** | boot reconciliation: positionEnd missing on empty portfolio → clean empty (not Orange) | G8-P11 | +0.5h |
| **22** | boot reconciliation: phantom positions → ManualRecovery TWAP liquidation (not adoption) | G8-P5 | +0.5h |
| **22** | boot: check /dev/shm/aegis_emergency.json first, then /app/logs/emergency_state.json | G8-P1 | +0.2h |
| **New AT** | AT-14c, AT-18h, AT-19c, AT-20c, AT-88c, AT-93h, AT-111h, AT-120c, AT-132c, AT-235c, AT-241b | all G8-P | +1.5h test |

**Total v27 hours delta**: ~+13h (391h → ~404h)
**Total v27 acceptance tests**: ~293 (282 + 11 new)

---

## SECTION 8: CONVERGENCE ANALYSIS

G8 represents the first audit where the majority of bullets targeted **interactions between v26 fixes** rather than standalone implementation gaps. This is the expected pattern as the architecture matures:

- G1-G4: Retail-level bugs (missing guards, wrong data types)
- G5-G6: Concurrency and ordering bugs (Semaphore patterns, WAL format)
- G7: Fourth-order interactions (PID 1 signals, settlement calendars, phantom positions)
- **G8: Fix-interaction bugs** (watchdog I/O path shares the hung resource, β→0 zero-heat approves leverage, dividend price inflation breaks ATR)

**Expected G9 pattern**: Protocol-level bugs — IBKR API state machine edge cases, Tokio executor starvation under specific scheduling patterns, Polars Arrow memory layout assumptions on ARM vs x86.

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v26.md — Generated 2026-03-10*
*Source: Gemini G8 adversarial audit of AEGIS_MASTER_PLAN_v26.md*
*Result: 11 priority fixes (G8-P1 through G8-P11), 3 improvements (G8-I1 through G8-I3), 5 operational fixes (G8-O1 through G8-O5), ~20 duplicates/FUD, ~9 academic deferrals*
*Next: AEGIS_MASTER_PLAN_v27.md*
