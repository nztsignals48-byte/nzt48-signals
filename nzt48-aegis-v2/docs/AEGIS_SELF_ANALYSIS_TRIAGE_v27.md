# AEGIS V2 — SELF-ANALYSIS TRIAGE v27
### G9 "Institutional Syndicate" Audit of AEGIS_MASTER_PLAN_v27.md

**Version**: 27.0 | **Date**: 2026-03-10 | **Audit Source**: Gemini G9 "Institutional Syndicate" adversarial audit of v27
**Triage By**: Claude (second-order adversarial review)
**Output**: AEGIS_MASTER_PLAN_v28.md

---

## CLASSIFICATION LEGEND

- **G9-P** — Genuine new priority finding: accepted, injected into v28
- **DUPLICATE** — Already fixed in v20-v27
- **ACADEMIC** — Deferred post-Crucible
- **FUD** — Incorrect or unfounded
- **NOTED** — Minor, valid, no structural change

---

## SECTION 1: G9 PRIORITY FIXES (ACCEPTED)

---

### G9-P1 — /dev/shm Erased on Container Restart: Emergency State Lost

**Bullets**: #1 [FLAW], Red Team #A1, Probability: 100%, Severity: Fatal

**Root cause**: v27-FIX-1 writes `aegis_emergency.json` to `/dev/shm/` (tmpfs inside the container). When the watchdog calls `_exit(1)`, the container terminates. Docker's `restart: unless-stopped` policy creates a **new container lifecycle**. Docker mounts a fresh empty tmpfs for `/dev/shm` in the new container. The emergency state is evaporated by Docker before the Rust engine ever reads it.

`/dev/shm` is the correct choice for **same-lifecycle** inter-process communication (Polars scratch, halt_ack.json for a process that's still running). It is **categorically wrong** for **cross-lifecycle** (watchdog trip → container restart → boot recovery) state persistence.

**Triage**: ACCEPT. The fix requires a host-mapped Docker volume — a named bind mount that bypasses Docker's ephemeral container filesystem and writes directly to the host filesystem. The host filesystem persists across container restarts (it only clears on host reboot, which is rare and planned).

```yaml
# docker-compose.yml — add to aegis-v2 service:
volumes:
  - ./emergency_state:/app/emergency  # host-mapped, persists across restarts
```

```rust
// watchdog.rs — write to host-mapped volume (NOT /dev/shm):
const EMERGENCY_PATH: &str = "/app/emergency/aegis_emergency.json";

let payload = format!("{{\"ts\":{},\"pid\":{}}}", now, unsafe { libc::getpid() });
let _ = std::fs::write(EMERGENCY_PATH, &payload);
// Proceed to _exit(1) — no fallback needed (host volume is always writable)
unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
std::thread::sleep(Duration::from_secs(5));
unsafe { libc::_exit(1) };
```

On boot: check `/app/emergency/aegis_emergency.json`. One path. One mount. Always writable. Survives container restart.

`/dev/shm/halt_ack.json` (Phase 17, v27-FIX-8) is **correctly** using /dev/shm — that file is read by monitoring scripts in the **same container lifecycle** while the engine is still running. No change needed there.

**Acceptance test**: AT-18i — Simulate EBS freeze + watchdog trip → `_exit(1)` → container restarts → verify `/app/emergency/aegis_emergency.json` present in new container → boot enters Yellow → reconciliation runs → file deleted.

**Phase**: 8 (watchdog.rs + docker-compose.yml)

---

### G9-P2 — O_NONBLOCK on Regular EBS Files: Linux Ignores It, Watchdog Deadlocks

**Bullets**: #2 [FLAW], Red Team #A1 corollary

**Root cause**: v27-FIX-1 added a "best-effort" fallback write to `/app/logs/emergency_state.json` via `O_NONBLOCK` open. Linux defines `O_NONBLOCK` semantics only for special files (FIFOs, character devices, sockets). For **regular files**, the kernel ignores `O_NONBLOCK` entirely — `open()` and `write()` block synchronously regardless. If EBS is hung, the fallback write blocks the watchdog thread, preventing `_exit(1)` from being reached. The fix (G9-P1) that writes to the host-mapped volume at `/app/emergency/` makes the EBS fallback path entirely unnecessary.

**Triage**: ACCEPT. Remove the EBS fallback entirely. The watchdog now has exactly one write target: `/app/emergency/aegis_emergency.json` (host-mapped volume, always writable). No fallback. No O_NONBLOCK placebo.

```rust
// watchdog.rs (final form):
const EMERGENCY_PATH: &str = "/app/emergency/aegis_emergency.json";
let _ = std::fs::write(EMERGENCY_PATH, &payload);  // host volume: not affected by EBS hang
// _exit(1) always reached
unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
std::thread::sleep(Duration::from_secs(5));
unsafe { libc::_exit(1) };
```

**Acceptance test**: AT-18i (same as G9-P1) — one test covers both fixes.

**Phase**: 8 (watchdog.rs — remove O_NONBLOCK fallback path)

---

### G9-P3 — Phantom TWAP Panic: Missing ADV for Un-Cached Tickers

**Bullets**: #3 [FLAW], Red Team #A2, Probability: 85%, Severity: High

**Root cause**: v27-FIX-5 liquidates phantom positions via `executioner.liquidate_twap()`. The Executioner V2 sizes TWAP slices using ADV (Average Daily Volume) from the Ouroboros-populated calibration cache. A phantom position recovered from IBKR at boot-time may be a ticker not in the current Ouroboros universe (e.g., it was traded manually, is a non-standard asset, or was dropped from the universe since the position was opened). ADV lookup returns `None`. If not handled: divide-by-zero or `unwrap()` panic → container crashes → restarts → finds phantom again → TWAP panic again → permanent death loop.

**Fix**: For phantom positions, bypass the ADV-bounded TWAP. Use a **time-naive fallback algorithm**: 10 equal slices, 60 seconds apart. This requires no calibration data and reliably liquidates any position over ~10 minutes.

```rust
pub async fn liquidate_phantom_twap(&self, position: PhantomPosition) {
    // No ADV lookup — phantom positions lack calibration context
    let slice_qty = (position.qty / 10).max(1);  // 10 equal slices
    log::warn!("PhantomTWAP {}: {} slices of {} qty over 10 minutes",
               position.ticker, 10, slice_qty);
    for i in 0..10 {
        self.place_market_sell(position.ticker_id, slice_qty,
                               StrategyId::ManualRecovery).await;
        if i < 9 {
            tokio::time::sleep(Duration::from_secs(60)).await;
        }
    }
}
```

Caller: `executioner.liquidate_phantom_twap(phantom).await` (not `liquidate_twap` which requires ADV).

**Acceptance test**: AT-235d — Boot with phantom position for ticker NOT in Ouroboros cache (no ADV data); verify time-naive TWAP (10 slices × 60s) executes without panic; verify slot freed after completion.

**Phase**: 14 (executioner_v2.rs — add `liquidate_phantom_twap` method) + 22 (boot reconciliation: use `liquidate_phantom_twap` for phantoms)

---

### G9-P4 — EvictionCooldown Blocks Emergency Safety Lines

**Bullets**: #4 [FLAW], Severity: High

**Root cause**: v27-FIX-2 adds a 5-minute `EvictionCooldown` per evicted ticker. If a ticker is evicted due to Error 322 but one second later a Mega-Runner state or active position hedge requires a subscription to that exact ticker, the `SubscriptionManager` blocks it for up to 5 minutes. This delays critical safety-line subscriptions for active carry positions.

**Fix**: `EvictionCooldown::can_subscribe()` checks the `StrategyId` of the requesting subscription. `ActivePosition` and `ManualRecovery` requests **bypass the cooldown entirely**. Only `HotScanner` and `RotationScanner` requests are subject to the 5-minute ban.

```rust
impl EvictionCooldown {
    fn can_subscribe(&self, ticker_id: TickerId, strategy_id: StrategyId) -> bool {
        match strategy_id {
            // Active position safety lines: always allowed regardless of cooldown
            StrategyId::ActivePosition | StrategyId::ManualRecovery => true,
            // Scanner subscriptions: subject to 5-minute cooldown
            _ => self.evicted_at.get(&ticker_id)
                .map(|t| t.elapsed() > self.cooldown)
                .unwrap_or(true),
        }
    }
}
```

**Acceptance test**: AT-20d — Error 322 eviction of ticker A → 30 seconds later, ActivePosition subscription request for ticker A → verify cooldown bypassed → subscription granted → active carry line protected.

**Phase**: 11 (subscription_manager.rs — pass StrategyId to `can_subscribe`)

---

### G9-P5 — Chandelier Dividend Adjustment Fails for Leveraged ETPs

**Bullets**: #6 [FLAW], Severity: High

**Root cause**: v27-FIX-4 adjusts `highest_high` downward by the raw dividend cash amount (`highest_high -= dividend_amount`). For a 3x leveraged ETP, the price drops by approximately `3 × dividend_yield × underlying_close` on ex-date, NOT by the raw dividend cash amount of the underlying. If the underlying pays £0.50/share cash dividend and the ETP trades at £100, the ETP drops ~1.5% (£1.50) not £0.50. Subtracting the wrong magnitude severs the Chandelier stop incorrectly.

**Fix**: Calculate the ex-date price drop as a **percentage** of the ETP's own price, not as the raw cash amount of the underlying:

```rust
// On ex-date detection for leveraged ETPs:
let dividend_yield_pct = get_dividend_yield_pct(ticker, today, &corp_action_blocklist);
// dividend_yield_pct = dividend_amount / underlying_close × leverage_factor
// (all three fields stored in corp_action_blocklist.json from Ouroboros step 2)
let etp_drop = current_price * dividend_yield_pct;
self.highest_high = (self.highest_high - etp_drop).max(current_price);
log::info!("Chandelier ETP highest_high adjusted -{:.4} ({:.2}%) for dividend on {}",
           etp_drop, dividend_yield_pct * 100.0, ticker);
```

`corp_action_blocklist.json` schema: add `dividend_yield_pct: f64` field (computed by Ouroboros as `dividend_amount / underlying_close × leverage_factor`). The `leverage_factor` (1×, 2×, 3×) already in the ISA registry.

**Acceptance test**: AT-88d — QQQ3.L (3× leveraged): underlying dividend yield = 0.5% → ETP expected drop = 1.5%. Inject `dividend_yield_pct = 0.015`. Verify `highest_high` reduced by `current_price × 0.015` (not by raw £0.50 cash). Verify Chandelier stop recalculated correctly.

**Phase**: 14 (chandelier_exit.rs) + 16 (Ouroboros step 2: compute `dividend_yield_pct`)

---

### G9-P6 — Universe Cache Resurrects Delisted Tickers

**Bullets**: #7 [FLAW], Severity: Medium

**Root cause**: v27-FIX-7 merges a partial contractDetailsEnd timeout result with `universe_cache.json` (previous day's snapshot). If a ticker was delisted overnight or underwent a ticker change, it exists in the cache but not in today's live IBKR universe. The merged cache reintroduces dead tickers. On the next scanning cycle, these cause `Error 200: No security definition found` — consuming IBKR pacing budget, blowing the 100-line budget on dead subscriptions, and generating log noise.

**Fix**: After every contractDetailsEnd cycle (full or partial+merge), mark any ticker in the merged universe that returns Error 200 as `DELISTED` in the cache. On subsequent Ouroboros runs: purge `DELISTED` entries from `universe_cache.json` before merge.

```python
# In subscription_manager.rs — Error 200 handler:
200 => {
    log::warn!("Error 200 for req_id {}. No security definition. Marking delisted.", req_id);
    if let Some(ticker) = self.pending_contract_details.get(&req_id) {
        self.mark_delisted_in_cache(ticker);  // writes to universe_cache.json
    }
}

# In Ouroboros cache merge:
prev_universe = load_json('universe_cache.json')
# Purge any tickers marked delisted from previous cycles
active_prev = {k: v for k, v in prev_universe.items()
               if not v.get('delisted', False)}
merged = {**active_prev, **partial_received}
```

**Acceptance test**: AT-19d — universe_cache.json contains ticker DEAD.L (delisted); partial contractDetailsEnd results do NOT include DEAD.L; merged universe passed to Thompson Sampler; on next scan, Error 200 received for DEAD.L → `delisted: true` written to cache; next merge excludes DEAD.L.

**Phase**: 11 (subscription_manager.rs: Error 200 handler) + 16 (Ouroboros cache merge: filter delisted)

---

### G9-P7 — β→0 max_historical Returns None for IPO/New ETP: Wrong Default

**Bullets**: #8 [FLAW], Severity: Medium

**Root cause**: v27-FIX-3 looks up `max_cvar_heat_30d` from `asset_volatility.json` and returns `DEFAULT_MAX_HEAT = 0.95` if no history exists. This is correct behaviour — BUT the G9 audit correctly identifies that if `asset_volatility.get(ticker)` is `None` AND some downstream code path incorrectly `unwrap()`s the result before the `unwrap_or(DEFAULT_MAX_HEAT)`, there's a panic vector. This is a code hygiene fix, not a logical error.

**Triage**: ACCEPT WITH MODIFICATION. G9's proposed fix of defaulting to `0.15 CVaR` for new assets is WRONG — 0.15 is LOW risk, which would approve leverage into an IPO. v27's `DEFAULT_MAX_HEAT = 0.95` (near-certain veto) is correct for new assets. The fix is to add an explicit comment and make the `unwrap_or` pattern defensive:

```rust
if beta.abs() < 1e-8 {
    // Fail-safe: unknown volatility = maximum observed risk
    // DEFAULT_MAX_HEAT = 0.95 ensures IPOs and new ETPs face maximum veto pressure
    let max_heat = self.asset_volatility
        .get(ticker)
        .and_then(|v| {
            // Additional guard: if max_cvar_heat_30d is exactly 0.0, treat as missing data
            if v.max_cvar_heat_30d > 0.0 { Some(v.max_cvar_heat_30d) } else { None }
        })
        .unwrap_or(DEFAULT_MAX_HEAT);  // 0.95 — always veto if no history
    log::warn!("EvtBetaNearZero ticker={} max_heat={:.3} (history={})",
               ticker, max_heat,
               self.asset_volatility.contains_key(ticker));
    return Ok(CvarHeat::from(max_heat));
}
```

Explicitly guard against `max_cvar_heat_30d == 0.0` (indicating uninitialized data in the JSON). No change to the 0.95 default — G9's suggestion of 0.15 is rejected as FUD.

**Acceptance test**: AT-93i — IPO ticker with no entry in `asset_volatility.json`; β=1e-10 injected; verify `DEFAULT_MAX_HEAT=0.95` returned (not 0.15); verify log shows `history=false`; verify RiskGate vetoes.

**Phase**: 15 (cvar_heat.rs — defensive guard on `max_cvar_heat_30d == 0.0`)

---

### G9-P8 — reqMarketDataType(3) on Every Error 2106 Disrupts Active Tick Streams

**Bullets**: #3 [FLAW], Severity: Medium

**Root cause**: v27-FIX-9 re-sends `reqMarketDataType(3)` on every Error 2106 (Data Farm Restored). IBKR data farms flap frequently during market hours (2104/2106 cycles are common, sometimes multiple per hour). Each re-send of this global command causes the gateway to momentarily pause all active feed streams to process the configuration change. This creates artificial tick gaps during the OFI/COF calculation window — exactly the data corruption risk the engine is designed to avoid.

**Fix**: Add an `AtomicBool` flag `is_data_type_set`. Send `reqMarketDataType(3)` only once after `nextValidId` and then only if the **TCP socket itself disconnects** (full reconnect). Data farm flapping (2104/2106 within an existing TCP session) does NOT require resending — the gateway retains the configuration across data farm restarts within the same TCP session.

```rust
static IS_DATA_TYPE_SET: AtomicBool = AtomicBool::new(false);

fn next_valid_id(&mut self, order_id: i32) {
    self.next_order_id.store(order_id, Ordering::SeqCst);
    // Only set on fresh TCP connection (nextValidId always fires on reconnect)
    if !IS_DATA_TYPE_SET.load(Ordering::Relaxed) {
        self.client.req_market_data_type(3);
        IS_DATA_TYPE_SET.store(true, Ordering::Relaxed);
        self.wal.write(WalPayload::ReqMarketDataTypeSent { trigger: "nextValidId" });
    }
}

fn connection_closed(&mut self) {
    // TCP disconnect — reset flag so next_valid_id re-sends on reconnect
    IS_DATA_TYPE_SET.store(false, Ordering::Relaxed);
}

fn error(&mut self, req_id: i32, error_code: i32, error_msg: &str) {
    match error_code {
        2104 => log::warn!("Data farm connection broken"),
        2106 => {
            // Data farm restored within same TCP session — do NOT resend reqMarketDataType
            // Gateway retains configuration. Only log.
            log::info!("Data farm restored (Error 2106). reqMarketDataType unchanged (already set).");
        }
        // ... other codes
    }
}
```

**Acceptance test**: AT-14d — Inject Error 2104 then Error 2106 (data farm flap within same session); verify `reqMarketDataType(3)` NOT re-sent; verify no tick gap in active streams. AT-14e — Inject full TCP disconnect + reconnect; verify `reqMarketDataType(3)` re-sent on new `nextValidId` callback; verify IS_DATA_TYPE_SET reset on disconnect.

**Phase**: 8 (ibkr_broker.rs — IS_DATA_TYPE_SET AtomicBool + connection_closed reset)

---

## SECTION 2: DUPLICATES AND FUD

### Duplicates

| G9 Bullet | Classification | Already Fixed In |
|-----------|---------------|-----------------|
| "Watchdog _exit(1) corrupts WAL" | DUPLICATE | v24-FIX-1 (SIGTERM + 5s grace) |
| "reqOpenOrders causes Error 3200" | DUPLICATE | v21-FIX-2 |
| "Telegram 429 no backoff" | DUPLICATE | v26-FIX-7 + v27-FIX-10 |
| "WAL replay Orange on timeout" | DUPLICATE | v24-FIX-4 |
| "POLARS_MAX_THREADS not set" | DUPLICATE | v20-FIX-2 |
| "β→0 panic" | DUPLICATE (v27-FIX-3 already fixed; G9-P7 is hardening) | v27-FIX-3 |
| "contractDetailsEnd hangs forever" | DUPLICATE (v27-FIX-7 already fixed) | v26-FIX-3 + v27-FIX-7 |
| "float CRC32 mismatch" | **FUD** (documented v26) | v26 FUD patterns |
| "VaR not sub-additive" | **FUD** (documented v26) | v26 FUD patterns |

### FUD Classification

| G9 Bullet | Dismissal |
|-----------|-----------|
| "Telegram HALT logging is async-buffered → operator blind" | FUD. The Rust `log` crate buffers but does NOT defer logger output indefinitely. Under stress, log output may be delayed milliseconds, not seconds. The operator confirmation mechanism is `/dev/shm/halt_ack.json` (immediately written by Python `poll_task`) and the direct halt_channel.put — neither of which goes through the Rust log buffer. The log message is informational. Operator confirmation pathway is correct. |
| "Polygon /upcoming vs IBKR reqTradingHours timezone string mismatch" | Partially valid concern (noted for Phase 16 TDD) but severity overstated. IBKR format `20260310:0800-1630` is trivially parseable. This is a string parsing task, not an architectural flaw. NOTED for Phase 16 test coverage. |
| "Phantom position Mega-Runner hedge (G9-P4)" | Valid concern, correctly addressed by G9-P4. But the stated "4 minutes and 59 seconds" consequence is worst-case. In practice, a new Mega-Runner setup would request a scanner subscription (which can use any other available slot), not specifically require the evicted ticker. Severity is overstated but fix is correct. |
| "β→0 should default to 0.15 CVaR for new assets" | **FUD**. 0.15 = LOW risk = approve leverage. 0.95 = HIGH risk = veto. For an IPO with no history, 0.95 is the correct conservative default. G9 proposes the wrong direction. |

---

## SECTION 3: OPERATIONAL FIXES

### G9-O1 — Confirm halt_ack.json /dev/shm Semantics

`/dev/shm/halt_ack.json` (v27-FIX-8, Phase 17) is read by monitoring scripts while the engine is **still running** (same container lifecycle). This use of /dev/shm is **correct** — the file is written and read within the same container lifecycle. No change needed. Explicitly document in Phase 17 comments: "halt_ack.json lives in /dev/shm intentionally — same-lifecycle IPC only."

**Phase**: 17 (telegram_reporter.py — add comment)

### G9-O2 — Host Volume Emergency Path: Directory Creation on First Boot

The host-mapped `./emergency_state:/app/emergency` volume requires the `./emergency_state` directory to exist on the host before `docker compose up`. If it doesn't exist, Docker creates it but with root ownership, which may cause permission issues. Add to deploy script: `mkdir -p emergency_state` before `docker compose up`.

**Phase**: 8 (deploy.sh + docker-compose.yml: add `mkdir -p` to startup script)

### G9-O3 — IBKR reqTradingHours String Parser: Explicit Timezone Normalization

Per G9 bullet on Polygon vs. IBKR holiday formatting: In Phase 16, when implementing the `reqTradingHours` cross-reference for non-US assets (v27-FIX-6), explicitly normalize IBKR's `YYYYMMDD:HHMM-HHMM;YYYYMMDD:HHMM-HHMM` format to UTC before comparison. Do NOT rely on implicit timezone assumptions.

**Phase**: 16 (Ouroboros) + 8 (ibkr_broker.rs: parse_trading_hours_to_utc() helper)

---

## SECTION 4: ACADEMIC DEFERRALS

| G9 Bullet | Reason |
|-----------|--------|
| Async log buffer latency analysis | Monitoring via halt_ack.json is the correct solution. Log latency is informational only. |
| ADV-bounded TWAP for phantom positions | G9-P3 (time-naive TWAP) is the correct pragmatic fix. ADV-bounded TWAP for phantoms adds complexity with no safety benefit. |
| Mega-Runner hedge subscription priority | Addressed by G9-P4 (ActivePosition bypass). Detailed Mega-Runner hedging protocol is Phase Q2+. |
| Hill estimator dynamic EVT threshold | Post-Crucible |
| Volume Profile TWAP slicing | Phase Q2+ |
| Neural Hawkes / DQN / DPDK | Phase Q3-Q4 |

---

## SECTION 5: G9 INJECTION SUMMARY (v27 → v28 AMENDMENTS)

| Phase | Amendment | Fix ID | Hours Delta |
|-------|-----------|--------|-------------|
| **8** | watchdog: write to host-mapped volume `/app/emergency/aegis_emergency.json` (NOT /dev/shm) | G9-P1 | +0.5h |
| **8** | watchdog: remove O_NONBLOCK EBS fallback entirely — host volume is sole write target | G9-P2 | +0.3h |
| **8** | docker-compose.yml: add `./emergency_state:/app/emergency` bind mount; deploy.sh: `mkdir -p emergency_state` | G9-P1/O2 | +0.3h |
| **8** | ibkr_broker.rs: IS_DATA_TYPE_SET AtomicBool — reqMarketDataType only on TCP reconnect, not on Error 2106 | G9-P8 | +1h |
| **11** | subscription_manager: EvictionCooldown.can_subscribe() takes StrategyId — ActivePosition/ManualRecovery bypass cooldown | G9-P4 | +0.5h |
| **11** | subscription_manager: Error 200 handler → mark ticker as delisted in universe_cache.json | G9-P6 | +1h |
| **14** | executioner_v2.rs: add `liquidate_phantom_twap()` — time-naive 10×60s slices, no ADV | G9-P3 | +1h |
| **14** | chandelier_exit.rs: dividend adjustment uses `dividend_yield_pct` (not raw cash amount) — scales with leverage | G9-P5 | +1.5h |
| **15** | cvar_heat.rs: β→0 guard adds `max_cvar_heat_30d == 0.0` check → DEFAULT_MAX_HEAT; explicitly rejects 0.15 | G9-P7 | +0.3h |
| **16** | Ouroboros step 2: compute `dividend_yield_pct = dividend_amount / underlying_close × leverage_factor` | G9-P5 | +0.5h |
| **16** | Ouroboros cache merge: filter `delisted: true` entries before merge | G9-P6 | +0.3h |
| **16** | parse_trading_hours_to_utc() helper for IBKR reqTradingHours format normalization | G9-O3 | +0.3h |
| **22** | boot reconciliation: use `liquidate_phantom_twap()` (not `liquidate_twap`) for phantom positions | G9-P3 | +0.2h |
| **New AT** | AT-14d, AT-14e, AT-18i, AT-19d, AT-20d, AT-35d, AT-88d, AT-93i, AT-235d | all G9-P | +1h test |

**Total v28 hours delta**: ~+8.7h (404h → ~413h)
**Total v28 acceptance tests**: ~302 (293 + 9 new)

---

## SECTION 6: CONVERGENCE ANALYSIS

G9 confirms the expected fifth-order pattern: **OS and runtime lifecycle mismatches** that arise from the interaction between Docker, Linux kernel guarantees, and IBKR's internal gateway state machine.

- G1-G4: Retail-level bugs (missing guards, wrong data types)
- G5-G6: Concurrency and ordering bugs (Semaphore patterns, WAL format)
- G7: Fourth-order interactions (PID 1 signals, settlement calendars, phantom positions)
- G8: Fix-interaction bugs (watchdog I/O path shares the hung resource, dividend ATR distortion)
- **G9: Docker/OS lifecycle mismatches** (/dev/shm container-scoped, O_NONBLOCK ignored for regular files, AtomicBool for reqMarketDataType state)

**Expected G10 pattern**: Calibration math edge cases — Kalman filter initialization on gap-open days, CUSUM threshold sensitivity during low-volatility regimes, Thompson Sampler prior contamination from first-week Crucible trades.

**Infrastructure seal assessment**: After G9-P1 and G9-P2, the watchdog emergency recovery path is **genuinely sealed**. There are no remaining trapdoors between deadlock detection and guaranteed recovery. The host-mapped volume survives: container restarts, Docker daemon restarts (volume persists), and EBS freezes (write goes to host filesystem, not EBS-backed container layer). It does NOT survive host reboots — acceptable because host reboots are planned events with operator presence.

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v27.md — Generated 2026-03-10*
*Source: Gemini G9 adversarial audit of AEGIS_MASTER_PLAN_v27.md*
*Result: 8 priority fixes (G9-P1 through G9-P8), 3 operational fixes (G9-O1 through G9-O3), ~9 duplicates/FUD, ~6 academic deferrals*
*Next: AEGIS_MASTER_PLAN_v28.md*
