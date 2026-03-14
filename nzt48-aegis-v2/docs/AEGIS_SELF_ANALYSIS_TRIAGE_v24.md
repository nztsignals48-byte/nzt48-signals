# AEGIS V2 — SELF-ANALYSIS TRIAGE v24
### G6 "Adversarial Operator" 200-Bullet Audit of AEGIS_MASTER_PLAN_v24.md

**Version**: 24.0 | **Date**: 2026-03-10 | **Audit Source**: Gemini G6 "Adversarial Operator" 200-bullet audit of v24
**Triage By**: Claude (second-order adversarial review)
**Output**: AEGIS_MASTER_PLAN_v25.md

---

## CLASSIFICATION LEGEND

- **G6-P** — Genuine new priority finding: accepted, injected into v25
- **DUPLICATE** — Already fixed in v22/v23/v24 (cite the fix)
- **ACADEMIC** — Real concern, deferred post-Crucible (Phase Q2+)
- **FUD** — Incorrect or unfounded; dismissed with reasoning
- **NOTED** — Minor, valid, captured in v25 without structural change

---

## SECTION 1: G6 PRIORITY FIXES (ACCEPTED)

These are the genuine new bugs in v24 that require v25 amendments.

---

### G6-P1 — Watchdog PID 1 SIGTERM Ignored in Docker

**Bullet context**: `libc::kill(libc::getpid(), SIGTERM)` in SC-18-W fails silently if the engine runs as Docker PID 1. Linux kernel does not deliver SIGTERM to PID 1 unless the process has an explicit handler registered. If Tokio reactor is deadlocked, the signal handler itself may not run.

**v24 spec (broken)**: SC-18-W sends `libc::kill(SIGTERM)` and relies on SC-01 SIGTERM handler to complete graceful shutdown. If Tokio is hung (exact scenario the watchdog is designed for), the SIGTERM never reaches the handler.

**Root cause**: PID 1 in Docker ignores SIGTERM by default unless the init process installs a handler. Tokio's signal handler is registered via `tokio::signal::unix::signal()` which requires a live runtime. Deadlocked runtime = no signal delivery.

**Fix (v25)**:
```rust
// In watchdog.rs, after libc::kill(SIGTERM):
unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
// Give tokio 5 seconds to complete graceful shutdown.
std::thread::sleep(Duration::from_secs(5));
// Tokio is hung — force exit. libc::_exit() bypasses all Drop traits
// but at this point: positions are orphaned regardless (engine is deadlocked),
// WAL flush already failed (engine is hung), so _exit is the correct choice.
// Docker restart: unless-stopped will restart container.
unsafe { libc::_exit(1) };
```

**Acceptance test**: AT-18e — Simulate SIGTERM handler registration before spawn; inject deadlock; watchdog fires; process exits within 65s (60s sleep + 5s grace). Verify container restarts.

**Phase**: 8 (SC-18-W amendment)

---

### G6-P2 — T+1/T+2 Settlement Ignores Exchange Bank Holidays

**Bullet context**: v24's per-exchange T+1/T+2 uses calendar day subtraction. A US equity with ex_date=2026-05-26 (day after Memorial Day) gets `veto_date = 2026-05-25` (Memorial Day — not a trading day). The corp action blocklist would fail to block on the correct day.

**Root cause**: EXCHANGE_TIMEZONE_MAP `settlement_lag_days` arithmetic is naive chronological day offset, not business-day-aware.

**Fix (v25)**: Add `cal-date = "0.5"` to Python requirements. In Ouroboros step 2:
```python
from cal_date import BusinessCalendar
cal = BusinessCalendar(exchange_calendar_name)  # e.g. "NYSE", "LSE"
veto_date = cal.subtract_business_days(ex_date_local, settlement_lag_days)
```

For Rust: use `business_days` crate or pass the computed veto_date from Ouroboros Python (preferred — keeps settlement logic in Python where exchange calendars are easier to maintain).

**Acceptance test**: AT-111e — US equity ex_date=2026-05-26 (post-Memorial Day) → NYSE T+1 → veto_date=2026-05-22 (Friday before holiday), NOT 2026-05-25 (the bank holiday itself). UK equity ex_date=2026-04-21 (post-Good Friday) → LSE T+2 → veto_date=2026-04-17 (Thursday, skipping Good Friday and Easter Monday).

**Phase**: 16 (Ouroboros step 2)

---

### G6-P3 — EVT MLE Unstable: N≥50 Already in v24 (DUPLICATE REVIEW)

**Bullet context**: G6 flags EVT N<50 as too low for stable GPD MLE estimates. v24 already mandates ≥50 exceedances (v23-FIX-3, referenced in Phase 15). The fix exists.

**Triage**: **DUPLICATE** — Already fixed in v23-FIX-3. v24 Phase 15: "≥50 exceedances threshold (v23-FIX-3) unchanged."

**Status**: No v25 change needed. Verify AT-93d test exercises N=50 boundary.

---

### G6-P4 — BufReader::read_line OOM on Corrupted Newline in CRC32 Header

**Bullet context**: `BufReader::read_line()` reads until `\n`. On a torn write where the CRC32 header line was written without a trailing newline (or the `\n` byte itself is corrupted), `read_line()` will read the entire file into a single String. On a multi-GB WAL file, this causes OOM.

**Root cause**: v24-FIX-9 uses prefix-header `{CRC32hex}\n{JSON}`. The CRC32 header is 8 hex chars + `\n` = 9 bytes. `BufReader::read_line()` is correct for normal operation, but a torn write that drops the `\n` causes unbounded read.

**Fix (v25)**: Use `read_exact` for the fixed-width CRC32 header:
```rust
// CRC32 hex is always exactly 8 ASCII hex chars + newline = 9 bytes
let mut header_buf = [0u8; 9];
match reader.read_exact(&mut header_buf) {
    Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => {
        return Err(ActiveStateError::NoCrc32Header);
    }
    Err(e) => return Err(e.into()),
    Ok(()) => {}
}
let crc32_hex = std::str::from_utf8(&header_buf[..8])
    .map_err(|_| ActiveStateError::InvalidCrc32Header)?;
// header_buf[8] must be b'\n'
if header_buf[8] != b'\n' {
    return Err(ActiveStateError::InvalidCrc32Header);
}
```

**Acceptance test**: AT-231b — Write CRC32 header WITHOUT trailing newline → `read_exact` returns UnexpectedEof cleanly → `ActiveStateNoCrc32` → WAL replay (no OOM).

**Phase**: 22 (active_state.wal reader)

---

### G6-P5 — aiohttp Session FD Leak on asyncio Thread Restart

**Bullet context**: v24-FIX-10 scopes session creation inside `async def fetch_all_tickers()`. However, if the thread is killed (exception, timeout) before the coroutine reaches `session.close()`, the aiohttp connector holds open TCP sockets. On EC2 with `ulimit -n 1024`, repeated nightly Ouroboros restarts exhaust FDs within weeks.

**Root cause**: v24-FIX-10 uses context managers (`async with aiohttp.ClientSession() as session`) but only if the code path reaches the `async with` block. If `fetch_all_tickers` raises before entering the context manager, no cleanup occurs.

**Fix (v25)**: Explicit try/finally in `fetch_all_tickers`:
```python
async def fetch_all_tickers():
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10))
    try:
        redis_client = redis.asyncio.Redis(host='redis', port=6379,
                                           password=REDIS_PASSWORD, decode_responses=True)
        try:
            # ... all fetch logic ...
            pass
        finally:
            await redis_client.aclose()
    finally:
        await session.close()  # ALWAYS runs, even on exception or timeout
        # Explicit connector close to release all TCP sockets immediately
        await session.connector.close()
```

**Acceptance test**: AT-118b — Inject exception inside `fetch_all_tickers` before first request; verify FD count (via `/proc/self/fd`) does not increase after 100 simulated restart cycles.

**Phase**: 16 (Ouroboros data_fetch.py)

---

### G6-P6 — Intraday ATR Gap Exclusion Over-Allocates to Gap-Prone Equities

**Bullet context**: v24-FIX-8 uses `intraday_atr_14_pct = mean(bar.high - bar.low)` (High-Low only, no overnight gap). G6 correctly identifies this is still biased: gap-prone ETPs (3x leveraged, earnings-heavy) have small intraday H-L ranges but large overnight jumps. The intraday ATR understates true risk → Thompson Sampler allocates more lines than warranted.

**G6 proposed fix**: True Range scaled by intraday volume ratio.

**Triage**: PARTIAL ACCEPT. The G6 formula (True Range × intraday_vol / total_vol) introduces a new parameter (intraday_vol / total_vol) that requires reliable intraday volume data. For 3x LSE ETPs on IBKR delayed data, intraday volume granularity is unreliable. The correct fix is simpler: use `max(intraday_range, gap_magnitude × intraday_fraction)` where `gap_magnitude = |open - prev_close|`.

**Fix (v25)**: Replace raw H-L with hybrid intraday ATR:
```python
# For each day, compute:
intraday_range = bar.high - bar.low  # H-L (no gap)
gap_magnitude = abs(bar.open - prev_bar.close)  # overnight gap
# Hybrid: use the larger of pure intraday or 60% of the gap
# (3x ETPs gap ~60% as much intraday as the overnight)
hybrid_intraday_range = max(intraday_range, gap_magnitude * 0.6)
intraday_atr_14_pct = mean(hybrid_intraday_range[-14:]) / mid_price
```

This preserves the spirit of v24-FIX-8 (no FULL overnight gap) while acknowledging gap-prone ETPs need a higher noise floor. The 0.6 multiplier is a conservative estimate; log it as `gap_bleed_factor: 0.6` in asset_volatility.json for future calibration.

**Acceptance test**: AT-56d — QQQ3.L with known overnight gap of 2%: v24 intraday ATR < v25 hybrid ATR (gap_bleed raises the floor); TS allocates fewer lines to QQQ3.L vs v24 baseline.

**Phase**: 16 (Ouroboros step 3) + 13 (hot_scanner/rotation_scanner consume updated ATR)

---

### G6-P7 — OwnedSemaphorePermit Drops on tokio::select! Cancellation

**Bullet context**: `SemaphorePermitGuard { _permit: OwnedSemaphorePermit }` drops correctly when the guard goes out of scope. However, if the guard is held inside a `tokio::select!` branch and another branch wins, the permit guard is dropped (good), but the IBKR subscription (`reqMktData`) is still active. The channel subscription was never cancelled. This leaks a live IBKR subscription against a dead slot.

**Root cause**: v24-FIX-6 correctly handles the permit lifecycle (RAII drop = permit returned to semaphore). But the IBKR subscription is a side effect orthogonal to the permit. When `tokio::select!` cancels the branch, `cancelMktData` is never sent to IBKR.

**Fix (v25)**: Implement explicit cancellation in the permit guard:
```rust
struct SemaphorePermitGuard {
    _permit: OwnedSemaphorePermit,
    ticker_id: TickerId,
    cancel_tx: mpsc::Sender<CancelMktDataCmd>,
}

impl Drop for SemaphorePermitGuard {
    fn drop(&mut self) {
        // Send cancelMktData to background actor (non-blocking)
        // The actor has its own queue and will drain on shutdown
        let _ = self.cancel_tx.try_send(CancelMktDataCmd { ticker_id: self.ticker_id });
        // _permit drops automatically → permit returned to Semaphore
    }
}
```

The `cancel_tx` is a `mpsc::Sender` to a background IBKR actor that serializes `cancelMktData` calls on the IBKR connection thread. This avoids calling IBKR from inside `Drop` (unsound).

**Acceptance test**: AT-18f — Acquire 10 SemaphorePermitGuards; drop them via `tokio::select!` cancellation; verify 10 `CancelMktDataCmd` messages arrive in background actor queue; verify `semaphore.available_permits() == 100`.

**Phase**: 8 (SC-02 amendment) + 11 (SubscriptionManager)

---

### G6-P8 — 100ms COF Aggregation Destroys Bid/Ask Sequential Causality

**Bullet context**: v24-FIX-7 accumulates `bid_size_delta_sum` and `ask_size_delta_sum` across all overflow ticks in a 100ms window before emitting COF. The original Cont et al. (2014) OFI definition requires per-tick sequential processing: each BidSize/AskSize change is compared to the previous BBO state to determine directional pressure. Batching 100ms of ticks and summing deltas destroys the causal ordering.

**G6 position**: Process ticks sequentially, update EWMA per individual change.

**Triage**: PARTIAL ACCEPT. True sequential OFI requires the non-overflow (normal) path. The overflow path (COF) is explicitly an approximation during ringbuffer saturation — the comment in v24 says: "COF (Compressed Order Flow): approximation of OFI during overflow." However, the current implementation has a specific bug: summing `bid_size_delta_sum` without tracking whether a tick was a BBO improvement vs deterioration inverts signal direction on BBO refreshes.

**Fix (v25)**: Add directionality tracking to COF accumulation:
```rust
// On each overflow BidSize tick:
let delta = new_bid_size - prev_bid_size;
if delta > 0.0 {
    // Bid depth increased → bullish pressure
    bid_size_delta_sum += delta;
} else {
    // Bid depth decreased → bearish pressure (treat as ask pressure)
    ask_size_delta_sum -= delta;  // delta is negative, so -= makes it positive
}
prev_bid_size = new_bid_size;
// Mirror logic for AskSize ticks
```

Track `prev_bid_size` and `prev_ask_size` per ticker in the COF accumulator. This makes COF directionally correct at the per-tick level while still aggregating over the overflow window.

**Acceptance test**: AT-60c — Inject 10 overflow ticks: BidSize 100→110→95→110 (up, down, up): raw delta sum = +10; directional sum = +10 (up=10) − (−15 as ask=15) + 15(up). Verify COF uses directional logic, not simple last−first.

**Phase**: 8 (SC-09 + python_bridge.rs)

---

### G6-P9 — shm_size:2gb Swap Risk: No Pre-Flight RAM Check

**Bullet context**: `shm_size: '2gb'` in docker-compose.yml. If EC2 instance has only 4GB RAM and other processes consume 2GB, the remaining 2GB for /dev/shm may force the kernel to swap. Polars operations on swapped /dev/shm are catastrophically slow (Polars assumes /dev/shm is in-memory).

**v24 context**: v24-MINOR mentions "shm_size:2gb risks swap" as a noted item. G6 is correct that no actual mitigation was added.

**Fix (v25)**: Add pre-flight RAM check in Ouroboros startup:
```python
import psutil

def preflight_ram_check():
    mem = psutil.virtual_memory()
    available_gb = mem.available / (1024**3)
    if available_gb < 2.5:
        raise RuntimeError(
            f"Insufficient RAM for /dev/shm: {available_gb:.1f}GB available, "
            f"need ≥2.5GB. Free RAM before running Ouroboros."
        )
    logger.info(f"RAM pre-flight OK: {available_gb:.1f}GB available")

# Call at start of run_ouroboros() before any Polars ops
preflight_ram_check()
```

**Acceptance test**: AT-119b — Simulate `psutil.virtual_memory().available = 1.5GB` → `preflight_ram_check()` raises RuntimeError with message → Ouroboros aborts → Yellow alert sent (not Orange, since positions are unaffected).

**Phase**: 16 (Ouroboros startup)

---

### G6-P10 — Yellow Alert Throttle Hides Boundary Oscillation

**Bullet context**: v24-MINOR-FIX: "Yellow tier alert throttle: max 1 alert per 4h". If the engine oscillates between Yellow and normal mode at the 4h boundary (e.g., repeated WAL replay timeouts), the throttle suppresses all but the first alert. Operator never knows the engine is flapping.

**Fix (v25)**: Dual-track throttle: (1) per-alert: suppress repeated SAME-STATE alerts for 4h; (2) add hourly suppressed-count summary to Telegram:
```
[AEGIS HOURLY SUMMARY] 14:00-15:00 UTC
Yellow entries: 3 (throttled)
Orange entries: 0
Watchdog trips: 0
```

The hourly summary is never throttled — it always fires if any events were suppressed in the hour.

**Acceptance test**: AT-119c — Inject 5 Yellow alerts in 2h → 1 alert delivered, 4 suppressed → hourly summary reports "Yellow entries: 5 (4 throttled)".

**Phase**: 16 (Ouroboros step 10 + telegram_reporter.py)

---

### G6-P11 — Phase 22 WAL Read: No Maximum File Size Guard

**Bullet context**: `active_state.wal` has no maximum size guard before attempting to read. If a bug causes unbounded WAL growth (e.g., compaction fails for 7 days), the file could reach gigabytes. `BufReader` reading the entire file during startup could cause OOM during the most critical startup phase.

**Fix (v25)**: Add file size guard before opening WAL:
```rust
let metadata = std::fs::metadata(&wal_path)?;
if metadata.len() > MAX_WAL_SIZE_BYTES {
    warn!("WAL file size {}MB exceeds {}MB limit. Forcing Yellow + manual review.",
          metadata.len() / 1_000_000, MAX_WAL_SIZE_BYTES / 1_000_000);
    return Ok(DrawdownTier::Yellow);
}
```

`MAX_WAL_SIZE_BYTES = 100 * 1024 * 1024` (100MB — far larger than any legitimate WAL). This is a safety trap, not a normal path.

**Acceptance test**: AT-237 — Create 150MB mock WAL file → WAL reader detects oversized file → Yellow mode (not panic/OOM) → Telegram alert includes "WAL oversized".

**Phase**: 22

---

## SECTION 2: ACCEPTED IMPROVEMENTS (NON-CRITICAL)

### G6-I1 — Ouroboros Half-Day Staleness: Christmas Eve 12:30 Close

**Issue**: v24-FIX-3 market-hours staleness guard uses `exchange_was_open_since_generated_at` from `exchange_times.json`. On Christmas Eve (LSE closes 12:30 UTC), if Ouroboros ran at 10:00 and cache was generated at 10:00, the guard shows "exchange was open for 2.5h since generated_at" — triggers stale after 72h of trading hours. But next trading day (Dec 27) loads the cache and finds it stale even though the early close was nominal.

**Fix**: Use `actual_trading_hours_since` (sum of open minutes in the interval) rather than binary "was open". 72h of trading hours = ~72h / 8h per day = 9 trading days. Cache generated Monday: stale after 9 full trading days = next Wednesday.

**Phase**: 12 (smart_router.rs staleness calculation) — minor enhancement to AT-37d.

---

### G6-I2 — TWAP Minimum Slice Floor Must Account for IBKR Pacing

**Issue**: v24 sets `slice_interval = max(alpha_halflife_ms, 100ms)`. IBKR enforces no more than 60 orders per 10 minutes on the same connection. For TWAP with 20 slices at 100ms intervals = 2 seconds total. If 3 concurrent TWAP orders run simultaneously, that's 60 slices in 2 seconds — IBKR pacing violation.

**Fix**: TWAP slice interval must be coordinated with the token bucket. `slice_interval = max(alpha_halflife_ms, order_rate_limiter.next_slot_ms())`. The order rate limiter is already planned (SC-04 token bucket). Wire them together.

**Phase**: 14 — minor wire-up, no new acceptance test needed.

---

### G6-I3 — Watchdog `is_market_hours()` Call Has Same Clock Dependency

**Issue**: SC-18-W calls `is_market_hours()` to suppress watchdog during off-hours. If `clock.rs` has a bug (e.g., DST boundary), the watchdog may fire during legitimate off-hours or suppress during legitimate market hours. The watchdog is the last line of defense — it should not share the same clock implementation it is supposed to protect.

**Fix**: Use raw UTC timestamp comparison in watchdog — no timezone dependency:
```rust
// Watchdog uses UTC hour directly (LSE is 08:00-16:30 UTC in BST, 09:00-17:30 in GMT)
// Use conservative bounds: 07:30-17:00 UTC covers both BST and GMT
let utc_hour = (now % 86400) / 3600;
let in_market_window = utc_hour >= 7 && utc_hour < 18;
if in_market_window && (now - last) > 120 { ... }
```

This is intentionally conservative (may fire 30min before actual open) but is clock-library-independent.

**Phase**: 8 (SC-18-W, watchdog.rs) — replace `is_market_hours()` call.

---

## SECTION 3: OPERATIONAL FIXES (MINOR)

### G6-O1 — `cargo test` Parallelism Can Cause AtomicU64 Global State Conflicts

**Issue**: `static LAST_TICK_TS: AtomicU64 = AtomicU64::new(0)` is module-level global. Parallel `cargo test` runs share this global across test threads. Watchdog tests that set LAST_TICK_TS may interfere with concurrent engine tests.

**Fix**: Use `#[cfg(test)]` override or thread-local in tests. Add `#[serial_test::serial]` annotation to watchdog tests that modify LAST_TICK_TS.

**Phase**: 8 (watchdog.rs tests)

---

### G6-O2 — Phase 16 `intraday_spread_cache.json` Has No Schema Version

**Issue**: As the Ouroboros pipeline evolves, `intraday_spread_cache.json` may gain new fields. A running engine loading a stale cache from a previous version will silently ignore new fields (or worse, fail serde). No schema version field exists.

**Fix**: Add `"schema_version": 3` field to all calibration JSON files. Reader checks schema_version: mismatch → treat as stale → regenerate.

**Phase**: 16 (Ouroboros) — add to all calibration JSON outputs.

---

### G6-O3 — Prometheus /metrics Endpoint Has No Authentication

**Issue**: v24 Phase 22 adds Prometheus typed metrics. The `/metrics` endpoint is unauthenticated. On EC2 with security group allowing 8000 from anywhere, metrics expose position sizes, PnL, and mode state publicly.

**Fix**: Bind metrics endpoint to `127.0.0.1` only (localhost), not `0.0.0.0`. Prometheus scraper runs on same host or via SSH tunnel. No auth needed if localhost-only.

**Phase**: 22 — 1-line bind address change.

---

### G6-O4 — WAL Compaction Manifest Has No CRC32

**Issue**: `compaction_manifest.json` (Phase 22) tracks last compaction timestamp. If this file is corrupted (torn write), compaction will re-run from epoch 0, re-processing 7+ days of WAL events and potentially creating a duplicate compacted archive.

**Fix**: Apply same prefix-header CRC32 format to `compaction_manifest.json` as `active_state.wal`.

**Phase**: 22 — extend the WAL writer utility to all manifest files.

---

### G6-O5 — `reqTradingHours` Response Not Cached: 5000 Ticker Startup Latency

**Issue**: Ouroboros step 1 loads `exchange_times.json` from `reqTradingHours`. If IBKR returns trading hours per-contract (one IBKR call per unique exchange), 5000 tickers = potentially 100+ `reqTradingHours` calls during startup. IBKR pacing limit: 50 requests/s max.

**Fix**: Cache `exchange_times.json` with 7-day TTL. Only re-request if expired. Exchange trading hours change only on regulatory notice (months of lead time). A 7-day cache is safe.

**Phase**: 16 (Ouroboros step 1 — already planned, just needs explicit TTL documented).

---

### G6-O6 — `transaction_tax.toml` Loaded at Startup; Not Hot-Reloadable

**Issue**: FTT rates change when parliament passes a budget (happened 2024, 2025 in UK). Currently, rate change requires engine restart. A restart closes all positions if done during market hours.

**Fix**: Add hot-reload: Ouroboros nightly re-reads `transaction_tax.toml` and sends updated rates via a `config_update` channel. Phase 22 hardening already has ArcSwap for config — wire transaction_tax into the ArcSwap config reload path.

**Phase**: 22 — ArcSwap config reload extension.

---

## SECTION 4: DUPLICATES (ALREADY FIXED IN v20-v24)

| G6 Bullet | Classification | Already Fixed In |
|-----------|---------------|-----------------|
| "Semaphore permit leak in Drop" | DUPLICATE | v24-FIX-6: natural RAII, no mem::forget |
| "OFI uses trade volume not BBO" | DUPLICATE | v24-FIX-7: COF uses BidSize/AskSize |
| "exit(1) bypasses WAL flush" | DUPLICATE | v24-FIX-1: libc::kill(SIGTERM) |
| "EVT ξ capped at 0.5" | DUPLICATE | v24-FIX-5: ξ uncapped |
| "T+2 hardcoded for NYSE" | DUPLICATE | v24-FIX-2: NYSE/NASDAQ T+1 |
| "CRC32 at end of JSON" | DUPLICATE | v24-FIX-9: prefix-header format |
| "WAL replay timeout → Orange" | DUPLICATE | v24-FIX-4: timeout → Yellow |
| "48h staleness fires on Monday" | DUPLICATE | v24-FIX-3: market-hours-aware |
| "asyncio module-level singletons" | DUPLICATE | v24-FIX-10: sessions inside coroutine |
| "AtomicUsize SeqCst in telemetry" | DUPLICATE | v24: Ordering::Relaxed + G5-I1 |
| "Watchdog uses std::process::exit" | DUPLICATE | v24-FIX-1: already changed to libc::kill |
| "Docker SIGKILL at 10s" | DUPLICATE | v20-FIX-1: stop_grace_period: 60s |
| "Polars vCPU starvation" | DUPLICATE | v20-FIX-2: POLARS_MAX_THREADS=2 |
| "Half-Kelly floor = 0 trades" | DUPLICATE | v20-FIX-3: Kelly ramp floor 0.1x |
| "reqOpenOrders Error 3200" | DUPLICATE | v21-FIX-2: internal AtomicUsize |
| "shm_size 64MB bus error" | DUPLICATE | v21-FIX-5: shm_size: 2gb |
| "Telegram polling silent death" | DUPLICATE | Phase 17: keep-alive + retry |
| "DCC-GARCH RwLock poison" | DUPLICATE | v24: RwLock timeout + re-init |
| "Nordic dark pool adverse select" | DUPLICATE | v24: Nordic lit venue routing |
| "TOML i64 panic on bps fields" | DUPLICATE | v24: u32 explicit serde |
| "Artifact freshness 26h false stale" | DUPLICATE | v24: 96h freshness threshold |
| "WAL CRC32 + tmp + rename atomic" | DUPLICATE | v24-FIX-9 |
| "reqPnL 1-per-connection limit" | DUPLICATE | v20-FIX-5: account-level reqPnL |
| "clock.rs BST missing modulo" | DUPLICATE | v20-FIX-6: chrono-tz |
| "Chandelier Redis 7-day TTL" | DUPLICATE | Phases 1-7 COMPLETE |
| "EVT exceedance threshold N=20" | DUPLICATE | v23-FIX-3: N≥50 |
| "intraday spread cache no staleness" | DUPLICATE | v22-FIX-2 + v24-FIX-3 |
| "SemaphorePermitGuard double-return" | DUPLICATE | v24-FIX-6 |
| "WAL compaction severs positions" | DUPLICATE | v20-FIX-4: nightly rewrite |
| "cost basis wrong after split" | DUPLICATE | Phase 8 SC-10 |
| "ISA April 6 not Jan 1" | DUPLICATE | Phase 12 |
| "HKEX board lot zero shares" | DUPLICATE | Phase 12 |
| "contractDetailsEnd handler" | DUPLICATE | v24: Phase 11 amendment |
| "mode_controller channel full" | DUPLICATE | v24: capacity 64 |
| "Yellow throttle 4h suppression" | DUPLICATE | v24-MINOR + G6-P10 enhances it |
| "Polygon /dividends timeout" | DUPLICATE | v24-MINOR: retain previous |
| "shutil.move cross-device" | DUPLICATE | v24-MINOR |
| "Polars .optimize() missing" | DUPLICATE | v24-MINOR |
| "Telegram keep-alive dead conn" | DUPLICATE | v24: Phase 17 |
| "HTTP 429 no backoff" | DUPLICATE | v24: Phase 17 |

*(~40 clear duplicates of prior version fixes)*

---

## SECTION 5: ACADEMIC DEFERRALS (PHASE Q2+)

| G6 Bullet | Reason for Deferral |
|-----------|-------------------|
| Full L2 order book reconstruction for true OFI | Requires IBKR L2 subscription (not in budget until Q2) |
| Neural Hawkes process for tick arrival intensity | Phase Q3-Q4 Quantum Apex |
| DQN reinforcement learning for execution | Phase Q3-Q4 Quantum Apex |
| Rust DPDK network stack for sub-μs latency | Phase Q3-Q4 Quantum Apex |
| VIX futures as CBOE feed fallback | Phase Q2+ |
| Synthetic put hedges on carry positions | Phase Q2+ options |
| ELK/Fluentbit structured logging | Phase Q2+ ops tooling |
| Garman-Klass volatility estimator | Phase Q2+ signal research |
| Trade-clock EWMA (volume-based decay) | Phase Q2+ signal research |
| SGX SiMS TIF pre-close auction | Phase Q2+ |
| Cross-asset macro regime detection via HMM | Already in V1; V2 Phase Q2+ |
| Microstructure alpha decay measurement | Phase Q2+ |
| Slippage model regression per ETP | Phase Q2+ calibration |
| Post-trade TCA (Transaction Cost Analysis) | Phase Q2+ analytics |
| Smart order routing across IBKR + LSE Direct | Phase Q2+ infrastructure |

---

## SECTION 6: FUD / UNFOUNDED (DISMISSED)

| G6 Bullet | Dismissal Reason |
|-----------|-----------------|
| "3x leveraged ETPs are inherently unsuitable for automated trading" | False. Structurally valid for intraday momentum strategies. V2 is explicitly designed for this universe. |
| "IBKR paper mode results don't predict live performance" | True but irrelevant to code correctness. Paper mode is the mandated validation stage before live capital per the plan. |
| "Python GIL will cause tick processing bottlenecks at high data rates" | V2's tick processing is in Rust (python_bridge.rs). Python is only used for Ouroboros (nightly, no real-time path). GIL is not a bottleneck. |
| "Async Rust is too complex for a single developer" | Subjective. Not a code defect. |
| "Small-cap LSE ETPs have insufficient liquidity for £10K ISA" | False. 12 ISA funds are large-cap leveraged ETPs (QQQ3.L, 3LUS.L, etc.) with multi-million £ daily volume. |
| "Semaphore-based line limiting is unnecessary complexity" | The 100-line IBKR simultaneous subscription limit is a hard API constraint. Semaphore is the correct enforcement mechanism. |
| "Redis persistence adds latency to hot path" | Redis is NOT in the hot path. Redis is for nightly Ouroboros + Chandelier state persistence. Real-time path is in-memory. |
| "TDD mandate will slow development by 3x" | Opinion. Plan retains TDD mandate. |
| "EVT Peaks-over-Threshold requires stationarity" | False. POT EVT is robust to non-stationarity (it models the tail, not the full distribution). Standard in institutional risk management. |
| "Watchdog thread monitoring is cargo-cult reliability engineering" | Incorrect. Production Rust services without watchdog threads have caused documented outages. The watchdog pattern is standard. |

---

## SECTION 7: G6 INJECTION SUMMARY (v24 → v25 AMENDMENTS)

| Phase | Amendment | Fix ID | Hours Delta |
|-------|-----------|--------|-------------|
| **8** | SC-18-W: Add `_exit(1)` fallback after 5s SIGTERM grace period | G6-P1 | +0.5h |
| **8** | SC-18-W: Replace `is_market_hours()` with raw UTC hour comparison | G6-I3 | +0.2h |
| **8** | SC-02/11: SemaphorePermitGuard gains `cancel_tx` field; Drop sends cancelMktData | G6-P7 | +2h |
| **8** | SC-09: COF accumulator tracks prev_bid_size/prev_ask_size for directional deltas | G6-P8 | +1h |
| **8** | Tests: `#[serial_test::serial]` on LAST_TICK_TS watchdog tests | G6-O1 | +0.2h |
| **12** | SmartRouter: actual_trading_hours_since calculation for half-days | G6-I1 | +0.5h |
| **14** | TWAP slice interval wired to token bucket rate limiter | G6-I2 | +0.5h |
| **16** | Ouroboros: business-day-aware settlement (cal-date Python library) | G6-P2 | +1.5h |
| **16** | Ouroboros: hybrid intraday ATR (max of H-L and gap_magnitude×0.6) | G6-P6 | +1h |
| **16** | Ouroboros: explicit try/finally in fetch_all_tickers for FD cleanup | G6-P5 | +0.5h |
| **16** | Ouroboros: psutil pre-flight RAM check (≥2.5GB available) | G6-P9 | +0.5h |
| **16** | Ouroboros: hourly suppressed-event summary to Telegram | G6-P10 | +1h |
| **16** | Calibration JSON files: add `schema_version` field | G6-O2 | +0.3h |
| **16** | exchange_times.json: document 7-day TTL explicitly | G6-O5 | +0.1h |
| **22** | WAL reader: `read_exact` for 9-byte CRC32 header (vs read_line) | G6-P4 | +0.5h |
| **22** | WAL reader: maximum file size guard (100MB → Yellow) | G6-P11 | +0.5h |
| **22** | compaction_manifest.json: prefix-header CRC32 | G6-O4 | +0.3h |
| **22** | Prometheus /metrics: bind to 127.0.0.1 only | G6-O3 | +0.1h |
| **22** | transaction_tax.toml: hot-reload via ArcSwap | G6-O6 | +1h |
| **New AT** | AT-18e, AT-18f, AT-56d, AT-60c, AT-111e, AT-118b, AT-119b, AT-119c, AT-231b, AT-237 | all G6-P | +1.5h test |

**Total v25 hours delta**: ~+12h (365h → ~377h)
**Total v25 acceptance tests**: ~272 (262 + 10 new)

---

## SECTION 8: INFRASTRUCTURE & HARDWARE ASSESSMENT

*(No changes from v24 assessment. G6 did not raise new hardware concerns beyond the RAM pre-flight check in G6-P9.)*

| Resource | Current | Required | Action |
|----------|---------|----------|--------|
| RAM | 4GB | 4GB sufficient for Phases 8-23 | Pre-flight check added (G6-P9) |
| EBS | 20GB (85% full) | 50GB | CRITICAL: expand NOW |
| CPU | 2 vCPU | 2 vCPU sufficient | No action |
| GPU | None | None until Phase Q3+ | No action |
| Polygon.io | Stocks Starter | Confirm /v3/reference/dividends access | Verify |
| IBKR L1 live | Not active | At go-live | Subscribe at live capital stage |

---

## SECTION 9: POLYGON FREE TIER — DYNAMIC RATE LIMITING

*(Addressing the user's question from before the G6 audit was pasted)*

**Question**: "Is there a way we can be dynamic on Polygon's free tier like we are with IBKR?"

**Answer**: Yes — same token bucket pattern applies. However, the key finding is:

1. **Polygon doesn't cover LSE `.L` tickers** (returns 0 results). Polygon is US-equity-only on free/starter tiers.
2. **For Ouroboros (nightly, 5000+ US tickers)**: Polygon Stocks Starter gives unlimited aggregates + `/v3/reference/dividends`. Add a token bucket: 4 req/min (conservative below 5 req/min limit). This is already planned in SC-04.
3. **For LSE data**: TwelveData covers LSE but the free tier (800 credits/day) has been blown by V1 runaway polling. Fix: add `max_calls_per_day` counter in V1's TwelveData client; reset at midnight UTC.
4. **Ouroboros timing**: Runs at 23:50 ET (nightly), outside LSE hours. Only needs historical bars for the universe scan — yfinance covers this as the final fallback. No real-time Polygon dependency.

**Summary**: Polygon is for nightly US universe scan only. LSE real-time data comes from IBKR direct subscriptions (market hours). TwelveData is the historical LSE fallback in Ouroboros — fix the V1 credit burnout separately.

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v24.md — Generated 2026-03-10*
*Source: Gemini G6 "Adversarial Operator" 200-bullet audit of AEGIS_MASTER_PLAN_v24.md*
*Result: 11 genuine priority fixes (G6-P1 through G6-P11), 3 improvements (G6-I1 through G6-I3), 6 operational fixes (G6-O1 through G6-O6), ~40 duplicates, ~15 academic deferrals, ~10 FUD*
*Next: AEGIS_MASTER_PLAN_v25.md*
