# AEGIS V2 — SELF-ANALYSIS TRIAGE v25
### G7 "Institutional Syndicate" 200-Bullet Audit of AEGIS_MASTER_PLAN_v25.md

**Version**: 25.0 | **Date**: 2026-03-10 | **Audit Source**: Gemini G7 "Institutional Syndicate" 200-bullet adversarial audit of v25
**Triage By**: Claude (second-order adversarial review)
**Output**: AEGIS_MASTER_PLAN_v26.md

---

## CLASSIFICATION LEGEND

- **G7-P** — Genuine new priority finding: accepted, injected into v26
- **DUPLICATE** — Already fixed in v20-v25 (cite the fix)
- **ACADEMIC** — Real concern, deferred post-Crucible (Phase Q2+)
- **FUD** — Incorrect or unfounded; dismissed with reasoning
- **NOTED** — Minor, valid, captured in v26 without structural change

---

## SECTION 1: G7 PRIORITY FIXES (ACCEPTED)

---

### G7-P1 — _exit(1) Without Emergency State Snapshot: Orphaned Positions Undetectable

**Bullet**: #1 (CRITICAL). `libc::_exit(1)` destroys all in-memory position state. When Docker restarts the container, the WAL replayer has no record of which positions were open at the moment of death — the WAL SystemShutdown event was never written (Tokio was hung). Orphaned IBKR positions persist. The engine boots in Yellow tier with zero position awareness.

**G7 proposed fix**: Write `emergency_state.json` containing open position IDs to a pre-allocated mmap file before calling `_exit`.

**Triage**: ACCEPT WITH MODIFICATION. The mmap approach introduces complexity during the most critical failure path. Simpler: write `emergency_state.json` using `std::fs::write` (synchronous, no Tokio dependency) BEFORE sending SIGTERM — at the moment the watchdog first detects the deadlock, before any shutdown sequence. This gives the recovery path a snapshot regardless of whether SIGTERM or `_exit` runs.

```rust
// In watchdog, before libc::kill(SIGTERM):
// 1. Write emergency snapshot to disk (sync, no Tokio)
let emergency_path = "/app/logs/emergency_state.json";
if let Ok(snapshot) = std::fs::read("/app/calibration/active_state.wal") {
    // Write the last known WAL bytes as emergency snapshot
    let _ = std::fs::write(emergency_path,
        format!("{{\"watchdog_trip_ts\":{}, \"wal_bytes\":{}}}", now, snapshot.len()));
}
// 2. Then proceed with SIGTERM + sleep(5) + _exit(1)
```

On boot, WAL replayer checks for `emergency_state.json`: if present AND WAL SystemShutdown event is absent → log `WatchdogEmergencyBoot` → force Yellow tier → Telegram alert with position count from last WAL. Operator can reconcile against IBKR positions before resuming.

**Acceptance test**: AT-18g — Simulate deadlock; watchdog fires; verify `emergency_state.json` written before `_exit(1)`; verify boot detects absence of SystemShutdown → Yellow + Telegram alert.

**Phase**: 8 (SC-18-W) + 22 (WAL boot recovery path)

---

### G7-P2 — cal-date Ignores Unscheduled Exchange Closures (Mourning Days, Weather)

**Bullet**: #2 (CRITICAL). `cal-date` uses static bank holiday arrays compiled at library build time. Unscheduled closures (UK national mourning day Sep 2022, NYSE Hurricane Sandy Oct 2012) are not in any static calendar. Settlement lag calculation is wrong for these dates — ISA settlement could cross an ex-dividend boundary.

**G7 proposed fix**: Integrate Polygon market status API to dynamically confirm actual trading days.

**Triage**: ACCEPT. Polygon already confirmed Starter+ and has a market status endpoint (`/v1/marketstatus/upcoming`). Ouroboros step 1 queries this endpoint during the nightly run and writes `market_status_cache.json` with the next 30 days of actual trading days. Settlement lag calculation in step 2 uses `market_status_cache.json` as ground truth, falling back to `cal-date` only if the cache is >48h old or the endpoint fails.

```python
# Ouroboros step 1 addition:
resp = polygon_session.get('/v1/marketstatus/upcoming', params={'apiKey': POLYGON_KEY})
upcoming = resp.json()  # list of market open/closed days
write_json('market_status_cache.json', {'days': upcoming, 'generated_at': now_iso()})

# In step 2, settlement lag:
actual_trading_days = load_market_status_cache()  # falls back to cal-date if stale
veto_date = subtract_n_trading_days(ex_date, lag, actual_trading_days)
```

**Acceptance test**: AT-111g — Inject a mock unscheduled closure (NYSE closed on a Wednesday) into `market_status_cache.json`; verify veto_date skips that day; verify `cal-date` would have given the wrong date.

**Phase**: 16 (Ouroboros step 1 + step 2)

---

### G7-P3 — WAL CRC32 Float Precision Desync Between Python Writer and Rust Reader

**Bullet**: #4 (HIGH) / #34 (RISK). Python's `json.dumps` and Rust's `serde_json` serialize f64 values differently at the extreme precision tail. `0.3333333...` may serialize as `0.3333333333333333` in Python and `0.3333333333333333` in Rust (same), but `0.1 + 0.2` serializes as `0.30000000000000004` in Python and `0.30000000000000004` in Rust — usually matching. However, platform-specific dtoa implementations (musl vs glibc) can produce different trailing digits. The CRC32 hash of different byte strings will differ → Rust refuses to load the artifact → Yellow tier.

**v25 context**: v24-FIX-9 introduced prefix-header CRC32. The CRC32 is computed by Python over the JSON string it wrote; Rust recomputes CRC32 over the JSON bytes it reads. As long as Python writes and Rust reads the same bytes, CRC32 matches. The actual issue is: **Rust never re-serializes and re-hashes** — it hashes the raw bytes from disk. So float precision desync between Python-write and Rust-read does NOT cause CRC32 mismatch — it would cause serde_json parse errors or logic errors downstream, not hash mismatch.

**Triage**: PARTIAL FUD for the stated CRC32 mismatch mechanism — CRC32 is hash-of-bytes-on-disk, both sides see identical bytes. HOWEVER, there is a real downstream issue: if Python writes `0.30000000000000004` and Rust deserializes it as f64, the round-trip is exact. But if Rust then re-serializes for comparison or display, it may produce different digits. The real risk is in the **compaction_manifest.json write from Rust**: Rust serde_json writes floats, Rust reads them back. Same platform = identical. This is FUD for cross-platform, irrelevant for single-EC2 deployment.

**Verdict**: **FUD** for CRC32 mismatch. NOTED as a reminder that all float fields in WAL/calibration JSON should use explicit decimal precision (`round(val, 8)` in Python, `format!("{:.8}", val)` in Rust) as defensive practice. No structural change to v26.

---

### G7-P4 — contractDetailsEnd State Machine Hangs Forever on Dropped Marker

**Bullet**: #10 (FLAW) / #8 (TOP 10). If IBKR drops the `contractDetailsEnd` TCP packet during a gateway reset, the state machine stays in "collecting" status indefinitely. Phase 11 `contractDetailsEnd` handler has no timeout — the `UniverseScanner` pipeline halts.

**Triage**: ACCEPT. Add `tokio::time::timeout(Duration::from_secs(15), collect_contract_details())` in the subscription_manager batcher. On timeout: log `ContractDetailsTimeout { req_id, received_count, expected_count }` → process the partial universe (do not abort) → continue pipeline.

**Acceptance test**: AT-19b — Inject contractDetailsEnd drop after 3000 of 5000 tickers; verify 15s timeout fires; verify 3000-ticker partial universe is used; pipeline continues.

**Phase**: 11 (subscription_manager.rs)

---

### G7-P5 — Telegram 429 Sleep Blocks Emergency HALT Commands

**Bullet**: #22 (FLAW) / #100 (MISSING). When Telegram issues a 429 response, the v25 implementation sleeps `retry_after` seconds (up to 300s). During that sleep, the async polling loop is blocked. Incoming HALT commands (from operator) cannot be received for up to 5 minutes.

**Triage**: ACCEPT. Decouple send-path (which sleeps on 429) from receive-path (which must always poll). Use two separate tasks:
- **Poll task**: `getUpdates` long-poll, always running, never sleeps on 429 (429 only applies to sending, not polling).
- **Send task**: processes outbound alert queue; applies 429 backoff on send failures only.

```python
async def poll_loop():
    # NEVER sleeps on 429 — receiving is not rate-limited
    while True:
        updates = await get_updates(timeout=30)
        for update in updates:
            await handle_command(update)

async def send_loop():
    # Applies 429 backoff on send failures
    while True:
        msg = await send_queue.get()
        await send_with_backoff(msg)
```

**Acceptance test**: AT-132b — Inject HTTP 429 on send path (150s retry_after); send a HALT command via poll path; verify HALT command received and processed within 5s despite active 429 backoff on send side.

**Phase**: 17 (telegram_reporter.py)

---

### G7-P6 — EVT β→0 Division by Zero: NaN Propagation Crashes RiskGate

**Bullet**: #31 (RISK). If the EVT GPD scale parameter β approaches zero (observed during absolute-zero-volatility periods — halted assets, bank holidays), the CVaR formula divides by β. β=0 → NaN. If this NaN is unwrapped in Rust → panic → RiskGate crash.

**v25 context**: Phase 15 `cvar_heat.rs` already guards `ξ ≥ 1.0 → CVaRExceeded`. No guard exists for `β → 0`.

**Triage**: ACCEPT. Add explicit β guard:
```rust
if beta.abs() < 1e-8 {
    // Degenerate distribution — asset has effectively zero tail risk
    // (e.g., halted, zero volatility). Return zero CVaR heat.
    log::warn!("EVT beta near-zero ({:.2e}) for {}. Returning zero CVaR.", beta, ticker);
    return Ok(CvarHeat::zero());
}
```
Zero CVaR heat = RiskGate does not veto on tail grounds. The MinimumEntryGate and spread veto remain active.

**Acceptance test**: AT-93g — Inject EVT fit with β=1e-10; verify no panic; verify `CvarHeat::zero()` returned; RiskGate proceeds to next veto check.

**Phase**: 15 (cvar_heat.rs)

---

### G7-P7 — WAL Skip-Corrupt: Skipped PositionClosed = Phantom Position on Boot

**Bullet**: #21 (FLAW). v25 Phase 22 WAL replayer skips corrupted events with `WalEventCorrupt` log. If a `PositionClosed` event is the corrupted one, the replayer never processes the close. The engine boots believing the position is still open → allocates a safety-locked line → calculates CVaR heat for a phantom position → restricts capital permanently.

**Triage**: ACCEPT. When a WAL event is skipped as corrupt, log the `WalPayload` type if recoverable from the raw bytes. On boot, after WAL replay completes, reconcile positions against IBKR via `reqPositions`. Any position in WAL-replayed state that IBKR does not report → mark as `PhantomPosition` → forcibly close the slot → log `PhantomPositionReconciled`. This reconciliation already happens nightly (SC-10 reqPositions resync) but must also happen at boot after a corrupt-skip.

**Acceptance test**: AT-235b — Inject corrupted `PositionClosed` event in WAL; replay; verify phantom position detected during boot reconciliation; slot forcibly released; `PhantomPositionReconciled` logged.

**Phase**: 22 (WAL replayer + boot reconciliation)

---

### G7-P8 — Polygon 429 on Aggregates: No Jittered Backoff

**Bullet**: #49 (RISK). If Polygon returns HTTP 429 on the `/v2/aggs` endpoint during the Ouroboros nightly scan, v25 has no jittered backoff. The pipeline retries continuously until the DARK mode window closes.

**Triage**: ACCEPT. Add exponential backoff with jitter for all Polygon requests in Ouroboros:
```python
async def polygon_get_with_backoff(session, url, params, max_retries=5):
    for attempt in range(max_retries):
        resp = await session.get(url, params=params)
        if resp.status == 429:
            retry_after = int(resp.headers.get('Retry-After', 60))
            jitter = random.uniform(0, retry_after * 0.2)
            await asyncio.sleep(retry_after + jitter)
            continue
        return resp
    return None  # exhausted retries → skip ticker, continue pipeline
```

**Acceptance test**: AT-120b — Inject HTTP 429 with Retry-After:10 on Polygon aggregates for 3 consecutive calls; verify exponential backoff + jitter applied; verify pipeline continues after retries exhausted (skips ticker, does not abort).

**Phase**: 16 (Ouroboros data_fetch.py)

---

### G7-P9 — reqMarketDataType(3) Sent Before nextValidId: Command Dropped Under Load

**Bullet**: #41 (RISK). v25 SC-14 sends `reqMarketDataType(3)` as the first call in `connect()`. IBKR gateway requires `nextValidId` callback to confirm the connection is fully initialized before accepting data-type commands. Under load, the gateway may drop `reqMarketDataType(3)` if sent before `nextValidId` fires. The engine then operates on delayed data silently.

**Triage**: ACCEPT. Gate `reqMarketDataType(3)` on receipt of `nextValidId` callback:
```rust
// In ibkr_broker.rs:
fn next_valid_id(&mut self, order_id: i32) {
    self.next_order_id.store(order_id, Ordering::SeqCst);
    // NOW safe to send reqMarketDataType(3)
    self.client.req_market_data_type(3);
    log::info!("reqMarketDataType(3) sent after nextValidId({})", order_id);
}
```
Remove the `reqMarketDataType(3)` call from `connect()`. Add `ReqMarketDataTypeSent` WAL event for audit trail.

**Acceptance test**: AT-14b — Simulate delayed `nextValidId` (500ms after connect); verify `reqMarketDataType(3)` is sent only after `nextValidId` fires; verify no delayed-data operation before that point.

**Phase**: 8 (SC-14 amendment, ibkr_broker.rs)

---

### G7-P10 — IBKR Error 322 Not Handled: Distinct from Error 3200

**Bullet**: #87 (MISSING). v25 handles Error 3200 (pacing violation — reqOpenOrders ban). Error 322 (market data subscription limit exceeded — hard capacity) requires a different response: do NOT retry after a pacing timeout; instead, cancel a lower-priority subscription first, then re-subscribe. v25 has no Error 322 handler.

**Triage**: ACCEPT. Add Error 322 handler in subscription_manager.rs:
```rust
322 => {
    // Max market data subscriptions exceeded (hard capacity limit, not pacing)
    // Must cancel a lower-priority subscription before retrying
    log::error!("Error 322: Subscription capacity exceeded for req_id={}", req_id);
    self.evict_lowest_priority_subscription();
    // Retry original subscription after eviction
    self.retry_subscription(req_id);
}
```
`evict_lowest_priority_subscription()` removes the lowest-TS-score active scan subscription (not carry, not active position). Log `SubscriptionEvicted { ticker_id, reason: Error322 }`.

**Acceptance test**: AT-20b — Inject Error 322 for a new subscription request; verify lowest-priority scan subscription evicted; original subscription retried successfully; active_line_count ≤ 100.

**Phase**: 11 (subscription_manager.rs)

---

### G7-P11 — Chandelier Ratchet Missing Dividend Ex-Date Adjustment

**Bullet**: #56 (RISK) / #88 (MISSING). On dividend ex-date, the asset price drops by the dividend amount (typically 0.5-2% for 3x ETPs). The Chandelier trailing stop, set as a percentage below recent high, will interpret the dividend-driven price drop as a real move and trigger a false exit.

**Triage**: ACCEPT. In `chandelier_exit.rs`, on ex-date (from `corp_action_blocklist.json`): adjust the trailing stop downward by the dividend amount before evaluating the stop condition.
```rust
let adjusted_price = if is_ex_date(ticker, today, &corp_action_blocklist) {
    let div_amount = get_dividend_amount(ticker, today, &corp_action_blocklist);
    current_price + div_amount  // treat price as if dividend hadn't been stripped
} else {
    current_price
};
// Evaluate Chandelier stop against adjusted_price
```

**Acceptance test**: AT-88b — Inject 1% dividend ex-date for QQQ3.L; inject 1% price drop on ex-date; verify Chandelier stop NOT triggered (adjusted price above stop); verify WITHOUT fix, stop WOULD have triggered.

**Phase**: 14 (chandelier_exit.rs / exit_engine.rs) + 16 (corp_action_blocklist carries dividend amount)

---

## SECTION 2: ACCEPTED IMPROVEMENTS (NON-CRITICAL)

### G7-I1 — Docker cgroups Memory Limit Instead of psutil Host Check

**Bullet**: #5 (TOP 10) / #36 (RISK). psutil sees the EC2 host's physical RAM, not the Docker container's cgroup allocation. If the host is overprovisioned, psutil passes but the container hits its cgroup limit mid-Polars-run → OOM kill → entire engine down.

**Fix**: In `docker-compose.yml` for the Ouroboros container (or the nzt48 container running Ouroboros), add explicit memory limits:
```yaml
deploy:
  resources:
    limits:
      memory: 3g  # hard cap; kernel enforces via cgroup
```

Keep the psutil check as a soft pre-flight warning (not hard abort). The cgroup limit is the hard enforcement. Add to Ouroboros startup: also read `/sys/fs/cgroup/memory/memory.usage_in_bytes` for container-accurate current usage.

**Phase**: 8 (docker-compose.yml) + 16 (Ouroboros pre-flight)

---

### G7-I2 — CancelMktData Actor Must Prioritize Over Other IBKR Operations

**Bullet**: #6 (TOP 10) / #8 (FLAW). If the CancelMktData background actor queue is backed up, IBKR sees a new `reqMktData` before the corresponding `cancelMktData`, briefly exceeding 100 lines → Error 3200 disconnect.

**Fix**: CancelMktData messages get a dedicated high-priority channel. The IBKR actor polls the cancel channel first on every iteration before processing any other outbound message.

```rust
loop {
    // Drain ALL pending cancels first (priority)
    while let Ok(cmd) = cancel_rx.try_recv() {
        client.cancel_mkt_data(cmd.ticker_id);
    }
    // Then process other outbound messages
    if let Ok(msg) = outbound_rx.try_recv() {
        process_msg(msg);
    }
    tokio::task::yield_now().await;
}
```

**Phase**: 11 (cancel_mktdata_actor.rs)

---

### G7-I3 — WAL Quarantine Log for Corrupted Events

**Bullet**: #80 (IMPROVEMENT). When WAL events are skipped as corrupt, the raw bytes should be written to `quarantine.log` with byte offset, so engineers can forensically analyze EBS failure patterns.

**Fix**: On `WalEventCorrupt`: append raw bytes (hex-encoded) + offset + timestamp to `/app/logs/quarantine.log`. File is append-only, rotated weekly.

**Phase**: 22 (WAL replayer)

---

### G7-I4 — XETRA Randomized Uncrossing: T-5 Hardcode Misses 2-Minute Window

**Bullet**: #86 (MISSING). XETRA closing auction has a 2-minute randomized uncrossing window (16:58-17:00 CET, ±2 min randomization). Hardcoding T-5 minutes for pre-close positioning may place the order inside the randomized window, where it gets executed at auction price rather than limit price.

**Fix**: XETRA pre-close cutoff = T-8 minutes (16:52 CET) to guarantee placement before the randomization window opens.

**Phase**: 18 (exchange_profile.rs — XETRA pre-close offset)

---

## SECTION 3: OPERATIONAL FIXES (MINOR)

### G7-O1 — reqContractDetails Pagination: Buffer Truncation on US Universe

**Bullet**: #89 (MISSING). Requesting the full US universe via a single `reqContractDetails` call can exceed the IBKR socket buffer and truncate silently without triggering `contractDetailsEnd`. Solution: paginate in batches of 500 tickers per `reqContractDetails` call.

**Phase**: 11 (subscription_manager.rs batching logic)

---

### G7-O2 — PDF Report Cleanup Cron in Container

**Bullet**: #94 (MISSING). 2 PDFs generated daily will exhaust `/tmp` over time. Add cleanup: Ouroboros step 10 deletes PDFs older than 7 days from `/tmp` before generating new ones.

**Phase**: 17 (pdf_generator.py)

---

### G7-O3 — Italian FTT: Apply Per-ISIN, Not Per-Exchange

**Bullet**: #37 (RISK). Italian FTT (0.10% lit, 0.20% dark) applies only to equities of companies with market cap >€500M listed on Italian exchanges. `transaction_tax.toml` applying FTT to the entire XETRA exchange (which lists Italian equities) is overbroad.

**Fix**: FTT check: `if exchange == "BORSA_ITALIANA" AND market_cap > 500_000_000 EUR`. Not per-exchange. The TOML needs an `apply_per_isin: true` flag and a market_cap threshold field.

**Phase**: 18 (transaction_tax.toml + transaction_tax.rs)

---

### G7-O4 — JPY Zero-Decimal Precision on TSE Orders

**Bullet**: #58 (RISK) / #98 (MISSING). TSE rejects limit orders with decimal places for JPY-denominated assets. All JPY order prices must be integers. Add explicit truncation: `price_jpy = price_f64.floor() as i64`.

**Phase**: 19 (asian_exchange.rs — JPY order formatting)

---

### G7-O5 — Prometheus Counter Reset on Container Restart

**Bullet**: #30 (FLAW). Standard Prometheus counters reset to 0 on container restart. Grafana `rate()` function interprets the reset as a massive negative spike. Fix: use `counter_reset_adapter` in the Grafana query (`increase()` instead of `rate()`) — document this in the Phase 22 Prometheus setup notes. Alternatively, persist counter values to Redis and reload on boot.

**Phase**: 22 (Prometheus setup notes + consider Redis persistence)

---

### G7-O6 — reqPositions Boot Reconciliation After Watchdog Emergency Boot

**Bullet**: Extends G7-P7. After a watchdog emergency boot (detected by presence of `emergency_state.json` + absent WAL SystemShutdown), the engine MUST run `reqPositions` against IBKR before accepting any new orders. Currently not specified.

**Phase**: 8 (boot sequence in main.rs — add pre-trade reconciliation gate after emergency boot detection)

---

## SECTION 4: DUPLICATES (ALREADY FIXED IN v20-v25)

| G7 Bullet | Classification | Already Fixed In |
|-----------|---------------|-----------------|
| "libc::_exit bypasses WAL flush" | DUPLICATE (partial) — WAL can't flush if Tokio hung anyway; G7-P1 extends with emergency snapshot | v25-FIX-1 + G7-P1 extends |
| "cal-date static arrays miss planned holidays" | DUPLICATE — cal-date covers all statutory holidays; G7-P2 extends for unscheduled | v25-FIX-2 + G7-P2 extends |
| "BufReader OOM on torn CRC32" | DUPLICATE | v25-FIX-3: read_exact 9 bytes |
| "aiohttp FD leak" | DUPLICATE | v25-FIX-4: try/finally |
| "Hybrid intraday ATR" | DUPLICATE | v25-FIX-5 |
| "cancelMktData on permit drop" | DUPLICATE (G7-I2 enhances priority) | v25-FIX-6 |
| "COF directionality" | DUPLICATE | v25-FIX-7 |
| "psutil RAM check" | DUPLICATE (G7-I1 enhances with cgroup) | v25-FIX-8 |
| "Yellow throttle hourly summary" | DUPLICATE | v25-FIX-9 |
| "WAL size guard" | DUPLICATE | v25-FIX-10 |
| "Watchdog UTC arithmetic" | DUPLICATE | v25-FIX-11 |
| "SemaphorePermitGuard mem::forget" | DUPLICATE | v24-FIX-6 |
| "exit(1) bypasses Drop" | DUPLICATE | v24-FIX-1 |
| "EVT ξ capped" | DUPLICATE | v24-FIX-5 |
| "T+2 hardcoded" | DUPLICATE | v24-FIX-2 |
| "CRC32 at end of JSON" | DUPLICATE | v24-FIX-9 |
| "asyncio module-level singletons" | DUPLICATE | v24-FIX-10 |
| "WAL replay timeout → Orange" | DUPLICATE | v24-FIX-4 |
| "Docker SIGKILL at 10s" | DUPLICATE | v20-FIX-1 |
| "Polars vCPU starvation" | DUPLICATE | v20-FIX-2 |
| "OFI using trade volume" | DUPLICATE | v24-FIX-7 + v25-FIX-7 |
| "reqOpenOrders Error 3200" | DUPLICATE | v21-FIX-2 |
| "shm_size 64MB bus error" | DUPLICATE | v21-FIX-5 |
| "Telegram keep-alive" | DUPLICATE | Phase 17 |
| "DCC-GARCH RwLock poison" | DUPLICATE | v24: RwLock timeout |
| "Nordic dark pool" | DUPLICATE | v24: Nordic lit venue |
| "Artifact freshness 26h" | DUPLICATE | v24: 96h threshold |
| "WAL compaction severs positions" | DUPLICATE | v20-FIX-4 |
| "skip-corrupt WAL" | DUPLICATE (G7-P7 extends with reconciliation) | v25 Phase 22 |
| "Polygon 504 timeout" | DUPLICATE | v24-MINOR |
| "market-hours staleness guard" | DUPLICATE | v24-FIX-3 |
| "EVT N≥50" | DUPLICATE | v23-FIX-3 |
| "contractDetailsEnd handler" | DUPLICATE (G7-P4 adds timeout) | v24 Phase 11 |
| "Prometheus metric types" | DUPLICATE (G7-O5 adds restart note) | v24 Phase 22 |
| "ISA April 6 boundary" | DUPLICATE | Phase 12 |
| "HKEX board lot" | DUPLICATE | Phase 12 |
| "reqMarketDataType(3) first in connect" | DUPLICATE (G7-P9 adds nextValidId gate) | v20-FIX-8 |

*(~38 clear duplicates)*

---

## SECTION 5: ACADEMIC DEFERRALS (PHASE Q2+)

| G7 Bullet | Reason |
|-----------|--------|
| Hill estimator dynamic EVT threshold (#63) | Requires calibration data volume only available post-Crucible |
| Integer-basis WAL serialization (#64, #3) | TRIAGE: FUD for CRC32 mismatch (see G7-P3). Useful but not urgent |
| Microstructure Noise Ratio for TS prior (#69) | Phase Q2+ signal research |
| dashmap for active subscriptions (#79) | RwLock timeout already handles; dashmap = premature optimization |
| Volume Profile TWAP slicing (#72) | Phase Q2+ execution research |
| VIX term structure carry cap (#73) | Phase Q2+ macro overlay |
| SPI 200 futures pre-warm for ASX (#76) | Phase Q2+ Asia |
| DCC-GARCH volume-weighted cross-tz (#77) | Phase Q2+ |
| Dynamic FTT net-position tracking (#75) | Phase Q2+ tax optimization |
| ES futures volume weighting for DCC (#77) | Phase Q2+ |
| Garman-Klass volatility estimator (#10 TOP) | Phase Q2+ — H-L hybrid ATR is sufficient for Crucible |
| mmap emergency state file (#61) | SUPERSEDED by G7-P1 simpler std::fs::write approach |
| flume::unbounded for cancel channel (#68) | Premature optimization; tokio mpsc unbounded is correct |
| Neural Hawkes / DQN / DPDK | Phase Q3-Q4 Quantum Apex |

---

## SECTION 6: FUD / UNFOUNDED (DISMISSED)

| G7 Bullet | Dismissal Reason |
|-----------|-----------------|
| "OFI micro-sequencing illusion — IBKR sends net delta only" (#3 RED TEAM, #5 FLAW) | **PARTIALLY CORRECT but misapplied.** The overflow COF path processes BidSize/AskSize tick callbacks — IBKR delivers these as individual tick events, not 100ms aggregates. The 100ms is the ringbuffer overflow *window*, not the tick delivery interval. Each BidSize/AskSize callback is a discrete event with prev/current state. v25-FIX-7 directional tracking is correct and meaningful. |
| "JSON float CRC32 mismatch between Python and Rust" (#4 FLAW, #5 RED TEAM) | **FUD.** CRC32 is computed over the bytes on disk. Python writes bytes, Rust reads the same bytes. CRC32 of identical byte strings is identical. Float format differences would cause downstream logic errors, not CRC32 mismatch. See G7-P3 triage. |
| "VaR is not sub-additive → RiskGate aggregation broken" (#15 FLAW) | **Incorrect criticism.** AEGIS uses CVaR (Expected Shortfall), which IS sub-additive and coherent (Artzner et al. 1999). VaR is used only for display/reporting. The RiskGate sizes on CVaR. |
| "Cornish-Fisher VaR not sub-additive" (#15 FLAW) | Same as above. AEGIS uses GPD CVaR, not Cornish-Fisher VaR for sizing decisions. |
| "psutil sees host RAM not container" (#36 RISK) | Partially correct — psutil in a Docker container reads `/proc/meminfo` which reflects the host. G7-I1 addresses with cgroup limits. But this is a RISK, not a critical flaw — pre-flight is a soft warning, not a hard guarantee. |
| "Python GIL bottleneck on tick processing" | V2 tick processing is in Rust. Python is Ouroboros-only (nightly). GIL is irrelevant. |
| "3x leveraged ETPs unsuitable for automation" | False. See prior FUD dismissals. |
| "CUSUM threshold decays to zero during lunch" | The CUSUM is in Phase 13 (not yet implemented). This is a valid concern for Phase 13 implementation but not a v25 plan flaw. NOTED for Phase 13 TDD. |
| "Kalman filter initializes at zero" | Phase 13 not yet implemented. NOTED for Phase 13 TDD: initialize with first tick price as x_0. |
| "async Rust too complex for single developer" | Subjective. Not a code defect. |
| "MAX_CARRY_POSITIONS=6 limits capital efficiency" | By design. 6 is the Kelly-optimal cap at £10K ISA with £1,500 min entry. Adjustable post-Crucible. |

---

## SECTION 7: G7 INJECTION SUMMARY (v25 → v26 AMENDMENTS)

| Phase | Amendment | Fix ID | Hours Delta |
|-------|-----------|--------|-------------|
| **8** | SC-18-W: Write `emergency_state.json` via std::fs before SIGTERM; boot recovery path checks for it | G7-P1 | +1.5h |
| **8** | SC-14: Gate `reqMarketDataType(3)` on `nextValidId` callback, not `connect()` | G7-P9 | +0.5h |
| **8** | docker-compose.yml: `deploy.resources.limits.memory: 3g` on engine container | G7-I1 | +0.2h |
| **11** | subscription_manager: 15s timeout on contractDetailsEnd; process partial universe | G7-P4 | +1h |
| **11** | cancel_mktdata_actor: CancelMktData drain-first priority loop | G7-I2 | +0.5h |
| **11** | subscription_manager: Error 322 handler + `evict_lowest_priority_subscription()` | G7-P10 | +1.5h |
| **11** | subscription_manager: reqContractDetails pagination (500 tickers/batch) | G7-O1 | +1h |
| **14** | chandelier_exit.rs: dividend ex-date price adjustment before stop evaluation | G7-P11 | +1.5h |
| **15** | cvar_heat.rs: β→0 guard → `CvarHeat::zero()` (no panic) | G7-P6 | +0.5h |
| **16** | Ouroboros step 1: Polygon `/v1/marketstatus/upcoming` → `market_status_cache.json` | G7-P2 | +1.5h |
| **16** | Ouroboros step 2: use `market_status_cache.json` for settlement lag, fallback to cal-date | G7-P2 | +0.5h |
| **16** | Ouroboros data_fetch.py: Polygon jittered exponential backoff on 429 | G7-P8 | +0.5h |
| **16** | Ouroboros pre-flight: read `/sys/fs/cgroup/memory/memory.usage_in_bytes` for container-accurate check | G7-I1 | +0.3h |
| **17** | telegram_reporter.py: decouple send_loop and poll_loop into separate async tasks | G7-P5 | +1h |
| **17** | pdf_generator.py: cleanup PDFs >7 days old before generating new | G7-O2 | +0.2h |
| **18** | exchange_profile.rs: XETRA pre-close cutoff T-8 (not T-5) | G7-I4 | +0.2h |
| **18** | transaction_tax.toml + transaction_tax.rs: Italian FTT per-ISIN with market_cap threshold | G7-O3 | +0.5h |
| **19** | asian_exchange.rs: JPY order price integer truncation | G7-O4 | +0.3h |
| **22** | WAL replayer: boot reconciliation via reqPositions after corrupt-skip or emergency boot | G7-P7 + G7-O6 | +1.5h |
| **22** | WAL replayer: append corrupt bytes to quarantine.log with byte offset | G7-I3 | +0.3h |
| **22** | Prometheus: document `increase()` over `rate()` for counters; note Redis persistence option | G7-O5 | +0.2h |
| **New AT** | AT-14b, AT-18g, AT-20b, AT-88b, AT-93g, AT-111g, AT-119d, AT-120b, AT-132b, AT-235b | all G7-P | +1.5h test |

**Total v26 hours delta**: ~+14h (377h → ~391h)
**Total v26 acceptance tests**: ~282 (272 + 10 new)

---

## SECTION 8: CONFIRMED FUD PATTERNS (FOR FUTURE AUDITS)

G7 introduced several recurring FUD patterns to watch for in G8+:

1. **"Float precision causes CRC32 mismatch"** — CRC32 is hash-of-bytes; writer and reader see identical bytes. Only a risk if two different processes independently serialize the same logical value. Not the case in AEGIS (Python writes, Rust reads, same bytes).
2. **"VaR is not sub-additive"** — AEGIS uses CVaR (coherent). This criticism is always wrong for AEGIS.
3. **"OFI net delta = no sequence"** — COF processes individual IBKR tick callbacks, not 100ms snapshots. The 100ms is the overflow accumulation window, not the tick delivery interval.
4. **"MAX_CARRY_POSITIONS limits capital efficiency"** — by design, tunable post-Crucible.
5. **"3x ETPs unsuitable"** — dismissed in every audit. Always FUD.

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v25.md — Generated 2026-03-10*
*Source: Gemini G7 "Institutional Syndicate" 200-bullet adversarial audit of AEGIS_MASTER_PLAN_v25.md*
*Result: 11 genuine priority fixes (G7-P1 through G7-P11), 4 improvements (G7-I1 through G7-I4), 6 operational fixes (G7-O1 through G7-O6), ~38 duplicates, ~14 academic deferrals, ~11 FUD*
*Next: AEGIS_MASTER_PLAN_v26.md*
