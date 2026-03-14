# AEGIS SELF-ANALYSIS TRIAGE v22
### Claude Independent Adversarial Audit of AEGIS_MASTER_PLAN_v22.md
**Date**: 2026-03-09 | **Auditor**: Claude Sonnet 4.6 (Independent — not the Gemini G3 audit)
**Audit Scope**: AEGIS_MASTER_PLAN_v22.md (862 lines) + all v22 fixes (G3-P1 through G3-P10 + G3-CRITICAL-SAFETY)
**Methodology**: Second-order consequence analysis — what do v22's own fixes break? What did v22 not fix that v21 also missed?

> **Audit Philosophy**: Gemini's G3 audit identified surface-level bugs in v21. This audit attacks v22 at a deeper level: the second-order consequences of v22's own fixes, edge cases in the new code paths introduced by v22, and structural gaps that survived both v19/v20/v21/v22 audits. The standard applied is: "Would a Rust systems engineer and a quantitative risk manager both sign off on this?" If either would not, it is a finding.

---

## SECTION 1 — EXECUTIVE SUMMARY

| Category | Count | Action |
|----------|-------|--------|
| **G4-P (Priority Fixes — Genuine New Flaws)** | **10** | Accept all → inject into v23 |
| **G4-STRUCTURAL (Structural Gaps)** | **3** | Accept all → inject into v23 |
| **DUPLICATE** (already fixed in v19/v20/v21/v22) | 27 | No action |
| **ACADEMIC** (Phase Q2+ only) | 8 | Defer |
| **FUD** (non-issues, misidentified) | 2 | No action |

**Net new genuine findings**: 13 (10 priority + 3 structural)
**Total v23 fixes to inject**: 13

---

## SECTION 2 — G4 PRIORITY FIXES (P1 through P10)

### G4-P1 — AtomicUsize AcqRel vs SeqCst: Over-Ordering Penalty
**Severity**: Medium | **Phase**: 8, 11
**v22 Fix Attacked**: v22-FIX-1 (AtomicUsize(Ordering::SeqCst) for active_line_count)

**Finding**: v22-FIX-1 correctly replaces RwLock with AtomicUsize but mandates `Ordering::SeqCst` for ALL operations. `SeqCst` imposes a global memory fence on every read AND write on x86_64. For a counter that is incremented/decremented on every subscription ACK during market open (potentially hundreds per minute), this creates unnecessary cache-line contention. The correct ordering is:
- `fetch_add` / `fetch_sub` (write): `Ordering::AcqRel` — acquires on prior releases, releases to subsequent acquires. No global fence needed.
- `load` (read): `Ordering::Acquire` — sees all writes that released before.
- Only the `assert!(count <= 100)` gate check needs SeqCst IF it coordinates with a different thread that checks the Semaphore. But since Semaphore(100) enforces the budget constraint independently, the assert is purely diagnostic — `Acquire` suffices.

**The real risk**: In the current v22 spec, the SubscriptionManager uses AtomicUsize(SeqCst) for counting AND Semaphore(100) for budget. The Semaphore already provides the correct memory ordering for permit acquisition. The AtomicUsize is a *telemetry counter* only — it does not gate anything alone. SeqCst is overspecification and will create false confidence that the count is authoritative when the Semaphore is the actual gate.

**Fix**:
- `fetch_add(1, Ordering::AcqRel)` on reqMktData ACK
- `fetch_sub(1, Ordering::AcqRel)` on cancelMktData ACK
- `load(Ordering::Acquire)` for diagnostic reads
- Add comment: `// Semaphore(100) enforces budget; AtomicUsize is telemetry only. AcqRel sufficient.`
- Update AT-18b: grep confirms AcqRel not SeqCst in subscription_manager.rs

**v23 Action**: Replace all `Ordering::SeqCst` references in subscription_manager.rs spec with correct orderings. Update SC-02 terminal kickoff prompt.

---

### G4-P2 — OFI Volume-Weighted Aggregator: Zero-Denominator Divide
**Severity**: P0 (panic in production) | **Phase**: 8, 13
**v22 Fix Attacked**: v22-FIX-3 (volume-weighted OFI aggregator)

**Finding**: v22-FIX-3 specifies:
```
OFI = (Σbid_vol − Σask_vol) / (Σbid_vol + Σask_vol + ε)
```

The terminal kickoff prompt in v22 specifies `+ 1e-9` as epsilon. However, the v22 SC-09 deliverable description (Phase 8) states only:
```
OFI = (Σbid_vol − Σask_vol) / (Σbid_vol + Σask_vol)
```
No epsilon guard in the phase specification. The `+ ε` only appears in the terminal kickoff prompt SC-09 section.

**Failure Mode**: On first tick after market open reconnect, if the buffer drains before any `bid_vol` or `ask_vol` has been accumulated (e.g., all dropped ticks were non-quote updates with zero bid/ask volume), `Σbid_vol = 0` AND `Σask_vol = 0`. Division by zero → NaN → NaN propagates into QI EWMA → CUSUM false trigger → spurious trade signal.

Even with `+ 1e-9`: `OFI = 0 / 1e-9 = 0.0` (numerator is also 0). This is actually fine. But the inconsistency between the phase spec and the kickoff prompt means the implementation will be ambiguous.

**Fix**:
- Add explicit `+ 1e-9` epsilon guard to SC-09 deliverable text in Phase 8
- Add guard: `if bid_vol_sum == 0.0 && ask_vol_sum == 0.0 { emit QuoteImbalanceCompressed with ratio=0.5 (neutral) }` — not 0.0, because 0.0 triggers short-bias in the EWMA
- Add to AT-60 extended: zero-volume overflow test — all dropped ticks have vol=0 → OFI ratio=0.5 emitted (neutral), EWMA unchanged
- Update SC-09 text to match kickoff prompt (add `+ 1e-9`)

**v23 Action**: Fix Phase 8 SC-09 deliverable text. Add zero-volume guard. Add AT-60b.

---

### G4-P3 — EVT GPD Shape Parameter ξ Bounds: Unbounded MLE Failure
**Severity**: High | **Phase**: 15
**v22 Fix Attacked**: v22-FIX-9 (EVT Peak-Over-Threshold GPD fallback)

**Finding**: v22-FIX-9 specifies GPD CVaR via MLE on tail exceedances. The GPD CVaR formula used:
```
CVaR_GPD = u + σ/(1-ξ) × ((n/k × α)^(-ξ) - 1) / ξ
```

This formula has two critical failure modes not addressed in v22:

1. **ξ = 0 (exponential tail)**: Division by ξ in the formula → undefined. Must use the exponential limit: `CVaR_exp = u + σ × (1 - ln(k/(n×α)))`. This case is common for equities in normal market conditions.

2. **ξ ≥ 1 (infinite-variance distribution)**: `σ/(1-ξ)` → negative or undefined. This can occur during true flash crashes. If MLE converges to ξ ≥ 1, the formula produces garbage. Must clamp: `ξ = min(ξ_mle, 0.5)` with a `GpdShapeExcessive { xi: f64, clamped_to: 0.5 }` WAL warning event.

3. **MLE convergence failure**: With exactly 20 exceedances (minimum threshold in v22), MLE is unstable. Minimum recommended for GPD MLE is N=50. v22's `≥20 exceedances` threshold is too low.

**Fix**:
- Add ξ=0 special case: `if xi.abs() < 1e-6 { CVaR = u + sigma * (1.0 - (k as f64 / (n as f64 * alpha)).ln()) }`
- Add ξ clamp: `xi = xi.clamp(-0.5, 0.5)` with WAL event if clamped
- Raise minimum exceedances threshold from 20 → 50
- Add AT-93c: ξ=0 case returns exponential CVaR formula; AT-93d: ξ>1 clamped to 0.5, WAL event logged

**v23 Action**: Amend Phase 15 cvar_heat.rs spec with ξ bounds. Update EVT threshold to ≥50. Update AT-93b/93c/93d.

---

### G4-P4 — EXCHANGE_TIMEZONE_MAP: Missing Exchange Clearing Cutoff Times
**Severity**: Medium | **Phase**: 16
**v22 Fix Attacked**: v22-FIX-7 (EXCHANGE_TIMEZONE_MAP per-exchange corp action timezone)

**Finding**: v22-FIX-7 correctly maps exchange timezones for ex-date normalization. However, the implementation only converts the ex-date to exchange local midnight. For corp action settlement, the relevant barrier is not midnight but the **exchange clearing cutoff time** — the last moment a position can be established that will settle before ex-date.

Specific failures:
- **HKEX (Hong Kong)**: Clearing cutoff is T+2. Ex-date = 2026-04-10 HKT. Must hold position by 2026-04-08 HKT close. The v22 veto only blocks on 2026-04-10 HKT (too late by 2 days).
- **ASX (Australia)**: T+2 settlement. Same issue — veto must fire 2 business days before ex-date.
- **LSE**: T+2 as well. Currently correct only because the existing veto applies 1-2 days before in practice (ex-date is already the record date + 1 business day on UK markets). Marginal.
- **TSE (Japan)**: T+2 also.

The v22 timezone fix solves the *timezone conversion* but not the *settlement lag*. The corp action blocklist should veto on `ex_date - 2_business_days` in the exchange's local calendar, not on `ex_date` itself.

**Fix**:
- Add `settlement_lag_days: u8` to `EXCHANGE_TIMEZONE_MAP`: LSE=2, XETRA=2, TSE=2, KRX=2, ASX=2, HKEX=2, NYSE=2
- Corp action veto: `veto_date = ex_date_local - settlement_lag_days (business days in exchange calendar)`
- Use `reqTradingHours` data to determine exchange business days (already fetched in Ouroboros step 1)
- Add AT-111c: HKEX ex-date 2026-04-10 HKT → veto_date = 2026-04-08 HKT (2 business days prior)

**v23 Action**: Amend Phase 16 EXCHANGE_TIMEZONE_MAP spec with settlement_lag_days. Amend Ouroboros step 2 corp action logic. Add AT-111c.

---

### G4-P5 — SemaphorePermitGuard Double-Return: Permit Inflation Attack
**Severity**: Medium | **Phase**: 8, 11
**v22 Fix Attacked**: v22-FIX-5 (SemaphorePermitGuard with Drop::drop())

**Finding**: v22-FIX-5 specifies `SemaphorePermitGuard(Arc<Semaphore>)` with `Drop::drop() → add_permits(1)`. This correctly prevents permit leak on panic. However, if the underlying tokio `SemaphorePermit` is NOT stored inside the guard (only the Arc<Semaphore> reference is stored), and if the tokio permit is dropped separately when it goes out of scope in the calling code, the permit is returned TWICE:
1. When the tokio SemaphorePermit drops at end of scope (tokio's built-in return)
2. When SemaphorePermitGuard drops and calls `add_permits(1)` explicitly

This inflates `available_permits()` above 100 over time, allowing the 100-line budget to be silently exceeded.

**The correct pattern**:
```rust
struct SemaphorePermitGuard {
    permit: Option<OwnedSemaphorePermit>,  // consumes the permit (forget tokio's return)
    semaphore: Arc<Semaphore>,
}
impl Drop for SemaphorePermitGuard {
    fn drop(&mut self) {
        // Drop the OwnedSemaphorePermit FIRST (this returns 1 permit to tokio)
        // OR: forget it and manually add_permits(1)
        // NEVER do both. Choose one path.
        drop(self.permit.take());
        // do NOT call self.semaphore.add_permits(1) here — OwnedSemaphorePermit.drop() already did it
    }
}
```

OR alternatively, use `forget()` + manual `add_permits(1)`:
```rust
impl Drop for SemaphorePermitGuard {
    fn drop(&mut self) {
        if let Some(permit) = self.permit.take() {
            std::mem::forget(permit);  // prevent tokio from returning it
            self.semaphore.add_permits(1);  // manually return exactly once
        }
    }
}
```

v22's spec is ambiguous — it says `add_permits(1)` in Drop but does not specify whether the underlying OwnedSemaphorePermit is stored or forgotten.

**Fix**:
- Specify EXPLICITLY: `SemaphorePermitGuard` stores `OwnedSemaphorePermit` via `acquire_owned()`. Drop impl calls `std::mem::forget(permit)` then `semaphore.add_permits(1)`. This ensures exactly one return path.
- Add unit test AT-18c: acquire 100 permits via SemaphorePermitGuard, drop all, verify `available_permits() == 100` (not 200, not 50).
- Add panic test: acquire 50, panic in 25 of them via catch_unwind, verify `available_permits() == 100`.

**v23 Action**: Amend SC-02 spec with explicit forget+add_permits pattern. Add AT-18c.

---

### G4-P6 — intraday_spread_cache.json Staleness: No Expiry Guard
**Severity**: Medium | **Phase**: 12, 16
**v22 Fix Attacked**: v22-FIX-2 (5-day median intraday spread cache)

**Finding**: v22-FIX-2 replaces EOD auction spreads with 5-day median intraday spreads. The cache is written by Ouroboros step 3 nightly. SmartRouter reads it at runtime for routing decisions.

**Missing**: No staleness guard. If Ouroboros fails for 3+ consecutive nights (e.g., Polygon API outage, IBC weekly restart window, EC2 maintenance), SmartRouter will be reading a cache that is 3-7 days old. During this window:
- Spreads for newly listed ETPs or ETPs with liquidity changes are wrong
- Zero-spread guard (v22-FIX-2) will not fire for ETPs that recently developed liquidity issues
- SmartRouter may route to direct equity based on stale sub-1bps spreads from a week ago

**Fix**:
- Add `generated_at: u64` (Unix timestamp) field to `intraday_spread_cache.json`
- SmartRouter: on load, check `now() - generated_at > 48 * 3600`. If stale → log `SpreadCacheStale { age_hours }` → force ETP routing for ALL direct equity candidates until cache refreshes
- Add AT-37c: inject cache with `generated_at` = 72h ago → SmartRouter routes all to ETP; logs SpreadCacheStale
- Ouroboros step 3: add `generated_at` field to output

**v23 Action**: Amend Phase 12 smart_router.rs spec with staleness guard. Amend Phase 16 Ouroboros step 3 with generated_at field. Add AT-37c.

---

### G4-P7 — active_state.wal CRC32: CRC32 Is Not a Cryptographic Hash
**Severity**: Low-Medium | **Phase**: 22
**v22 Fix Attacked**: v22-FIX-4 (active_state.wal CRC32 atomic write)

**Finding**: v22-FIX-4 uses CRC32 for integrity verification of `active_state.wal`. CRC32 is a cyclic redundancy check — it detects random bit errors but does NOT detect intentional corruption or certain systematic truncations. Specifically:

1. **Truncation detection**: If the file is truncated (crash mid-write), CRC32 of the truncated content will NOT match the stored CRC32 (which was computed over the full content). ✓ This works correctly.

2. **The real gap**: v22 appends `{"_crc32": <hex>}` as the LAST line of the WAL. But the v22 spec says "Strip last line, recompute CRC32 of remaining content". If the file is truncated EXACTLY at the CRC32 line boundary (i.e., the rename happened but the CRC32 line write was in flight), the remaining content is valid JSON but the stored CRC32 is absent. The engine would then fail to find the CRC32 line and... what? The spec does not specify this failure mode.

3. **Missing line spec**: What happens if `active_state.wal` has no `_crc32` field? Is this treated as: (a) legacy file (no CRC32) → load without verification? Or (b) corrupt → WAL replay? The v22 spec does not define this.

**Fix**:
- Specify explicitly: if no `_crc32` line found → treat as `ActiveStateNoCrc32` → log + fall back to WAL replay (same as mismatch). Never silently load without verification.
- CRC32 line format: use a JSON object with a sentinel key that cannot appear in normal WAL content: `{"__aegis_crc32__": "deadbeef12345678"}` — not just `{"_crc32": ...}` which could clash with a future WAL field
- Add AT-227c: write active_state.wal with no CRC32 line (legacy format) → engine detects missing CRC32 → falls back to WAL replay; logs ActiveStateNoCrc32

**v23 Action**: Amend Phase 22 active_state.wal spec with explicit no-CRC32 handling and sentinel key format. Add AT-227c.

---

### G4-P8 — Terminal Kickoff Prompt: SC-01 Uses ctrlc Crate — WRONG After v22
**Severity**: Medium | **Phase**: 8
**v22 Fix Attacked**: v22-FIX-6 context (tokio::signal mandate from v21 full triage)

**Finding**: The v22 terminal kickoff prompt SC-01 reads:
```
SC-01: SIGTERM handler in main.rs — ctrlc crate, flatten positions → wait 30s for fills →
write SystemShutdown WAL event → exit
```

However, the v21 full triage integration (AEGIS_SELF_ANALYSIS_TRIAGE_v20.md → AEGIS_MASTER_PLAN_v21.md Phase 8 Amendment G2-IN12) specifies:
> **G2-IN12**: ctrlc race — use only tokio::signal | Remove ctrlc crate | main.rs

The v21 plan (Phase 8 Additional Full Triage Amendments) explicitly mandates replacing `ctrlc` crate with `tokio::signal` to eliminate the ctrlc race condition (ctrlc spawns its own non-tokio thread which races with the tokio runtime). This fix survived into v22's Phase 8 amendment section (G2-IN12 is listed in v21 SC-18 per the triage doc). But the v22 terminal kickoff prompt SC-01 still says `ctrlc crate`.

The kickoff prompt is what the implementer will literally paste. If it contradicts the phase spec, the implementer follows the kickoff prompt.

**Fix**:
- Update terminal kickoff SC-01: replace `ctrlc crate` with `tokio::signal::ctrl_c() + tokio::signal::unix::signal(SignalKind::terminate())`
- Add explicit: `Do NOT use the ctrlc crate — it races with tokio runtime (G2-IN12)`
- SC-18 (tokio::signal only) should remain as a separate SC item (it IS already in Phase 8 from v21 full triage; v22 removed it from the SC table but the kickoff prompt must reflect it)

**v23 Action**: Update terminal kickoff prompt SC-01. Add ctrlc-banned note.

---

### G4-P9 — Thompson Sampler Prior σ_0: Hardcoded 0.05 Too Narrow for 3x ETPs
**Severity**: Medium | **Phase**: 13
**v22 Fix Attacked**: v22-FIX-10 context (σ_noise = max(0.02, atr_14_pct × 1.5))

**Finding**: Phase 13 rotation_scanner.rs specifies Gaussian-Gaussian Thompson Sampler with:
```
Prior: μ_0 = 0.0, σ_0 = 0.05
```

v22-FIX-10 makes σ_noise (observation noise) dynamic via ATR percentile. But the prior `σ_0 = 0.05` (initial uncertainty about asset's true PnL% per trade) is still hardcoded at 0.05 for all assets.

For 3x leveraged ETPs like QQQ3.L (typical daily range 3-9%), a prior of σ_0=0.05 is a 5% prior std on expected PnL. This is reasonable for unleveraged assets but UNDERESTIMATES the prior uncertainty for 3x ETPs — the TS will converge prematurely on 3x ETPs after only a few trades, before the noisy returns have averaged out. The TS then either over-allocates to a 3x ETP that happened to have a few lucky trades, or under-allocates to one that had unlucky early trades.

**Fix**:
- Per-asset σ_0 based on asset_volatility.json: `σ_0 = max(0.05, atr_14_pct × 3.0)` — wider prior for high-volatility assets
- This initialization runs once when asset first enters the TS (not per-tick)
- Add to Ouroboros step 8: compute initial σ_0 per asset, store in asset_volatility.json as `ts_prior_sigma_0`
- Add AT-56b: QQQ3.L with atr_14_pct=0.067 → σ_0 = max(0.05, 0.067 × 3.0) = 0.201; ASML with atr_14_pct=0.01 → σ_0 = 0.05 (floor)

**v23 Action**: Amend Phase 13 rotation_scanner.rs with dynamic σ_0. Amend Ouroboros step 8 to compute ts_prior_sigma_0.

---

### G4-P10 — asyncio Fix Scope: data_fetch.py Has Multiple Async Entry Points
**Severity**: Medium | **Phase**: 16, 17
**v22 Fix Attacked**: v22-IN17 (asyncio RuntimeError safe restart in data_fetch.py)

**Finding**: v22-IN17 fixes `ouroboros/data_fetch.py` with an asyncio safe restart pattern: catch `RuntimeError: Event loop is closed` → `asyncio.new_event_loop()` → restart. This is the same fix applied to `telegram_reporter.py` in Phase 17.

However, `data_fetch.py` in a typical Ouroboros implementation has MULTIPLE async entry points:
1. The main `asyncio.run(fetch_all_tickers())` call
2. Polygon API streaming (if websocket mode used) via separate event loop
3. IBKR EWrapper callbacks running in IBKR's own thread, which may use asyncio bridges

The v22 fix only addresses the outer `asyncio.run()` call (matching the telegram_reporter.py pattern). If the RuntimeError originates from an inner coroutine or a nested `asyncio.get_event_loop()` call within `fetch_all_tickers()`, the outer catch does not help.

**More critical**: Calling `asyncio.new_event_loop()` and then `asyncio.set_event_loop(new_loop)` inside an exception handler that is itself running within a half-closed event loop is undefined behavior in Python's asyncio. The correct pattern is to call `asyncio.new_event_loop()` in a FRESH thread, not in the exception handler of the dead loop.

**Fix**:
- The restart pattern must spawn a new thread: `threading.Thread(target=lambda: asyncio.run(fetch_all_tickers())).start()`
- The Ouroboros main process waits on the thread (join with timeout)
- Add AT-113b: simulate RuntimeError inside a nested coroutine within fetch_all_tickers → new thread spawned → fetch completes → Ouroboros pipeline continues

**v23 Action**: Amend Phase 16 asyncio RuntimeError fix with threading-based restart. Amend Phase 17 gate test AT-130 to match new pattern.

---

## SECTION 3 — G4 STRUCTURAL FINDINGS

### G4-S1 — No Watchdog: Engine Can Deadlock Silently for 30+ Minutes
**Severity**: High | **Phase**: 8 / new
**Category**: Structural gap survived all v19-v22 audits

**Finding**: The AEGIS V2 engine has a heartbeat (`aegis_heartbeat_ts` in Redis, v20-FIX-9, Phase 17), but the heartbeat is written by the ENGINE itself. If the engine deadlocks internally (e.g., tokio task queue saturated, all futures blocked on a semaphore that never resolves, or the Python bridge subprocess hangs), the engine CANNOT write its own heartbeat. The external monitor (V1 Python or Telegram reporter) will eventually detect the missing heartbeat — but the detection latency is up to 30 minutes (the heartbeat interval).

During a 30-minute deadlock in market hours, positions remain open with no Chandelier management. At 3x leverage, a 30-minute unmanaged move can represent 3-5% of equity.

**Missing**: A watchdog timer INSIDE the engine that fires independently of the tokio task queue. Tokio itself cannot be the watchdog for a deadlocked tokio runtime.

**Fix**:
- Add a `std::thread::spawn` watchdog thread (NOT tokio task) that fires every 60 seconds
- Watchdog checks the last tick processed timestamp from a `AtomicU64` updated by the market data handler on every tick
- If `now() - last_tick_ts > 120 seconds` during market hours: watchdog calls `std::process::exit(1)` → Docker restart policy brings engine back
- WAL event `WatchdogTripped { last_tick_age_secs }` written to a separate logfile (not the main WAL, since the WAL writer may also be deadlocked)
- Add to Phase 8 SC items: SC-18-W (Watchdog thread). Supercronic restart policy: `restart: unless-stopped` in docker-compose.yml (already present? verify)

**v23 Action**: Add SC-18-W watchdog thread to Phase 8. Phase 8 hours +1.5h → 49.5h.

---

### G4-S2 — SemaphorePermitGuard + Panic in async Context: UB via Unwind Through async
**Severity**: High | **Phase**: 8
**Category**: Structural gap in v22-FIX-5 implementation

**Finding**: v22-FIX-5 specifies `SemaphorePermitGuard` with `Drop::drop() → add_permits(1)` and a unit test using `catch_unwind`. In Rust, `catch_unwind` works for synchronous panics. However, the `SemaphorePermitGuard` will be used inside `async fn` (tokio tasks). Tokio's `task::spawn` does NOT propagate panics back to the calling task via `catch_unwind` — panics in spawned tasks are caught by tokio's task executor and result in a `JoinError::is_panic()` on the `JoinHandle`.

The scenario where `SemaphorePermitGuard` is most needed — a tokio task panicking while holding a permit — is EXACTLY the scenario where `catch_unwind` based testing DOES NOT apply. In tokio:

```rust
tokio::spawn(async move {
    let _guard = SemaphorePermitGuard::acquire(&semaphore).await;
    panic!("in async context");
    // Drop runs CORRECTLY here — tokio catches the panic and drops all locals
});
```

The `Drop` DOES run correctly in this case (tokio's panic handler drops all locals before the JoinError). But the test using `catch_unwind` in a synchronous context does not verify this async behavior.

**Fix**:
- Add async panic test: `tokio::spawn(async { acquire guard → panic })` → `.await` returns `JoinError` → verify `semaphore.available_permits() == 100` after joining 100 panic tasks
- This is more important than the catch_unwind test and should replace it (or be added alongside)
- Add to AT-18b: async context panic test (tokio::spawn, not catch_unwind)

**v23 Action**: Add async panic test to SC-02 spec. Update AT-18b to include tokio::spawn panic scenario.

---

### G4-S3 — WAL Replay on Restart: No Maximum Replay Time Guard
**Severity**: Medium | **Phase**: 22
**Category**: Operational gap survived v19-v22

**Finding**: Phase 22 specifies `active_state.wal` as a fast-path shortcut. If the fast-path fails (CRC mismatch, staleness, no CRC32 line), the engine falls back to "historical WAL replay". The WAL replay reconstructs open positions by replaying all WAL events since last compaction.

**Missing**: What if the WAL is 6 months old with 10,000+ events? A complete WAL replay could take 30-60 seconds. During this window:
- Market data ticks are arriving (engine not processing them)
- IBKR may send `reqPnL` updates (engine not handling them)
- The Chandelier has no stop prices (unmanaged positions)

There is no maximum replay time budget specified. If replay exceeds 30 seconds, the engine should HALT (not continue blindly into trading with stale position state).

**Fix**:
- Add replay time budget: `const WAL_REPLAY_TIMEOUT_SECS: u64 = 30`
- If WAL replay exceeds 30 seconds: `engine.set_drawdown_tier(DrawdownTier::Orange)` (close all positions) + write `WalReplayTimeout` event + continue with empty position state (assumes flat)
- Add `last_compaction_ts` field to `compaction_manifest.json`. If `now() - last_compaction_ts > 7 days` AND active_state.wal fast-path fails: immediately Orange-tier instead of attempting replay
- Add AT-227d: inject 10,000 synthetic WAL events with no active_state.wal fast-path → replay completes under 30s (or Orange tier fires with WalReplayTimeout)

**v23 Action**: Amend Phase 22 WAL replay spec with timeout guard. Add AT-227d.

---

## SECTION 4 — DUPLICATE ITEMS (Already Fixed)

The following 27 items were considered during this audit and confirmed as already addressed in v19/v20/v21/v22:

| # | Finding | Disposition | Fixed In |
|---|---------|-------------|---------|
| D-01 | RwLock on active_line_count | DUPLICATE | v22-FIX-1 (AtomicUsize) |
| D-02 | EOD auction spread in SmartRouter | DUPLICATE | v22-FIX-2 (intraday cache) |
| D-03 | QI suspension at peak alpha | DUPLICATE | v22-FIX-3 (OFI aggregator) |
| D-04 | active_state.wal non-atomic write | DUPLICATE | v22-FIX-4 (CRC32 + rename) |
| D-05 | Semaphore permit leak on panic | DUPLICATE | v22-FIX-5 (PermitGuard) |
| D-06 | bypass-permissions in impl plan | DUPLICATE | v22-FIX-6 (accept-edits) |
| D-07 | Corp action timezone LSE-only | DUPLICATE | v22-FIX-7 (EXCHANGE_TIMEZONE_MAP) |
| D-08 | CarryMonitor silent discard | DUPLICATE | v22-FIX-8 (UnauthorizedPnLStream) |
| D-09 | Gaussian CVaR in flash crash | DUPLICATE | v22-FIX-9 (EVT POT GPD) |
| D-10 | σ_noise 30-day lag | DUPLICATE | v22-FIX-10 (ATR percentile) |
| D-11 | Docker SIGKILL at 10s | DUPLICATE | v20-FIX-1 (stop_grace_period: 60s) |
| D-12 | Polars vCPU starvation | DUPLICATE | v20-FIX-2 (POLARS_MAX_THREADS=2) |
| D-13 | Half-Kelly + min entry = 0 trades | DUPLICATE | v20-FIX-3 (dynamic Kelly ramp) |
| D-14 | WAL compaction severs open positions | DUPLICATE | v20-FIX-4 + v21-FIX-9 |
| D-15 | reqPnL 1-per-connection limit | DUPLICATE | v20-FIX-5 (account-level) |
| D-16 | clock.rs BST missing % 86400 | DUPLICATE | v20-FIX-6 (chrono-tz) |
| D-17 | reqOpenOrders Error 3200 ban | DUPLICATE | v21-FIX-2 (internal AtomicUsize) |
| D-18 | Docker /dev/shm 64MB | DUPLICATE | v21-FIX-5 (shm_size: '2gb') |
| D-19 | Maillard CF domain violation | DUPLICATE | v21-FIX-3 (K>S²-1 check) |
| D-20 | reqPnL manual holdings crash | DUPLICATE | v21-FIX-10 (HashSet whitelist) |
| D-21 | ml_meta_model AEGIS 0-05 | DUPLICATE | DISABLED entirely |
| D-22 | NZX pre-subscribe at 22:55 UTC | DUPLICATE | Phase 11 |
| D-23 | APScheduler timezone | DUPLICATE | SC-08 |
| D-24 | reqMarketDataType(3) missing | DUPLICATE | v20-FIX-8, SC-14 |
| D-25 | TDD mandate | DUPLICATE | TDD MANDATE section |
| D-26 | ISA tax year boundary April 6 | DUPLICATE | Phase 12 isa_gate.rs |
| D-27 | Peg-to-mid dust guard | DUPLICATE | SC-06 |

---

## SECTION 5 — ACADEMIC ITEMS (Phase Q2+)

These 8 items are theoretically valid but require infrastructure not present until Phase Q2+:

| # | Finding | Disposition |
|---|---------|-------------|
| A-01 | GPD scale parameter σ maximum likelihood numerical stability (Newton-Raphson) | ACADEMIC — Phase Q2+ numerical methods |
| A-02 | Multi-asset EVT correlation (CoVaR) | ACADEMIC — Phase Q2+ |
| A-03 | Tick-by-tick bid/ask volume reconstruction from IBKR L1 (IBKR does not provide per-tick bid/ask volume in reqMktData type 1) | ACADEMIC — important limitation but mitigation is to use total trade volume as proxy |
| A-04 | Bayesian model averaging across CF/EVT/Gaussian for CVaR | ACADEMIC — Phase Q2+ |
| A-05 | Thompson Sampler: UCB1 vs Thompson comparison | ACADEMIC — Thompson is correct choice |
| A-06 | OFI decay function (exponential vs rectangular window) | ACADEMIC — Phase Q2+ signal research |
| A-07 | Sub-millisecond WAL write latency analysis | ACADEMIC — not a constraint at current scale |
| A-08 | Chaos: simultaneous IBC restart + Redis OOM + Python bridge crash | ACADEMIC — Phase Q2+ chaos engineering |

---

## SECTION 6 — FUD ITEMS (Non-Issues)

| # | Finding | Disposition | Rationale |
|---|---------|-------------|-----------|
| F-01 | CRC32 is weak vs SHA-256 for WAL integrity | FUD | CRC32 is sufficient for detecting random bit errors and truncation. We are not defending against adversarial WAL tampering. SHA-256 would add latency to every nightly write for zero operational benefit. |
| F-02 | AtomicUsize is not sufficient for multi-machine subscription tracking | FUD | AEGIS V2 is a single-machine deployment. The AtomicUsize assumption of a single process is correct by design. |

---

## SECTION 7 — v23 INJECTION SUMMARY

### Phase-by-Phase v23 Amendments

| Phase | v23 Amendment | Fix ID |
|-------|--------------|--------|
| **Phase 8** | SC-02: Replace `Ordering::SeqCst` with `AcqRel` for fetch_add/fetch_sub, `Acquire` for loads. Add comment clarifying Semaphore is the authority. Add AT-18c (100 permit acquire-drop cycle, verify ==100). | G4-P1 |
| **Phase 8** | SC-02: SemaphorePermitGuard stores `OwnedSemaphorePermit`. Drop impl: `std::mem::forget(permit)` then `semaphore.add_permits(1)`. Exactly one return path. Add AT-18c async panic test (tokio::spawn). | G4-P5, G4-S2 |
| **Phase 8** | SC-09: Add explicit `+ 1e-9` epsilon to OFI denominator in deliverable text (not just kickoff prompt). Add zero-volume guard: `if bid_vol_sum == 0.0 && ask_vol_sum == 0.0 → emit ratio=0.5`. Add AT-60b (zero-volume overflow). | G4-P2 |
| **Phase 8** | SC-18-W (NEW): std::thread::spawn watchdog thread. Checks AtomicU64 last_tick_ts every 60s. If stale >120s during market hours → `std::process::exit(1)`. Hours +1.5h. | G4-S1 |
| **Phase 8 kickoff** | SC-01 kickoff prompt: replace `ctrlc crate` with `tokio::signal`. Add `DO NOT use ctrlc crate (races with tokio — G2-IN12)`. | G4-P8 |
| **Phase 12** | smart_router.rs: staleness guard for intraday_spread_cache.json. `generated_at` field checked on load. If age >48h → SpreadCacheStale log → force ETP routing. Add AT-37c. | G4-P6 |
| **Phase 13** | rotation_scanner.rs: dynamic σ_0 per asset = max(0.05, atr_14_pct × 3.0). Read from asset_volatility.json `ts_prior_sigma_0` field. Add AT-56b. | G4-P9 |
| **Phase 15** | cvar_heat.rs: ξ=0 special case (exponential CVaR formula). ξ clamp to [-0.5, 0.5] with GpdShapeExcessive WAL event. EVT minimum threshold: ≥50 exceedances (not 20). Add AT-93c, AT-93d. | G4-P3 |
| **Phase 16** | Ouroboros step 2: corp action veto uses `ex_date_local - settlement_lag_days` (business days). settlement_lag_days from EXCHANGE_TIMEZONE_MAP (all major exchanges: 2). Add AT-111c (HKEX T+2 test). | G4-P4 |
| **Phase 16** | Ouroboros step 3: add `generated_at` Unix timestamp to intraday_spread_cache.json. | G4-P6 |
| **Phase 16** | asyncio RuntimeError fix in data_fetch.py: thread-based restart. `threading.Thread(target=lambda: asyncio.run(...)).start()`. Not `asyncio.new_event_loop()` in exception handler. Add AT-113b. | G4-P10 |
| **Phase 16** | Ouroboros step 8: compute `ts_prior_sigma_0 = max(0.05, atr_14_pct × 3.0)` per asset. Write to asset_volatility.json. | G4-P9 |
| **Phase 22** | active_state.wal CRC32 sentinel key: `{"__aegis_crc32__": "<hex>"}` not `{"_crc32": ...}`. Explicit handling for missing CRC32 line: `ActiveStateNoCrc32` log → WAL replay. Add AT-227c. | G4-P7 |
| **Phase 22** | WAL replay timeout: 30s budget. Exceeded → DrawdownTier::Orange + WalReplayTimeout WAL event. `last_compaction_ts` in compaction_manifest.json; if >7 days old + fast-path fails → immediate Orange. Add AT-227d. | G4-S3 |

### Hours Impact of v23 Additions

| Addition | Phase | Added Hours |
|----------|-------|-------------|
| SC-02 ordering fix + AT-18c | 8 | +0.5h |
| SemaphorePermitGuard explicit pattern + async test | 8 | +0.5h |
| SC-09 zero-volume guard + AT-60b | 8 | +0.5h |
| SC-18-W watchdog thread | 8 | +1.5h |
| Spread cache staleness guard + AT-37c | 12 | +0.5h |
| Dynamic σ_0 + AT-56b | 13 | +0.5h |
| EVT ξ bounds + threshold 50 + AT-93c/93d | 15 | +1.0h |
| Settlement lag T+2 + AT-111c | 16 | +1.0h |
| asyncio thread-based restart + AT-113b | 16 | +0.5h |
| ts_prior_sigma_0 in Ouroboros step 8 | 16 | +0.5h |
| CRC32 sentinel key + AT-227c | 22 | +0.5h |
| WAL replay timeout + AT-227d | 22 | +1.0h |
| **Total v23 additions** | | **+9.0h** |

**v23 Total Remaining: ~354h** (vs ~345h in v22, +9h for v23 additions)
**Acceptance tests**: ~248 (vs ~235 in v22, +13 new tests)

---

### Items Permanently Deferred (Post-Crucible, confirmed v23)

| Item | Reason |
|------|--------|
| All 8 academic items (A-01 through A-08) | Phase Q2/Q3/Q4 only |
| All items from v22 deferred table | Unchanged |
| lot-level cost basis | v22 deferred, confirmed |

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v22.md — Generated 2026-03-09*
*Triages: Claude independent adversarial audit of AEGIS_MASTER_PLAN_v22.md (NOT the Gemini G3 re-audit)*
*Methodology: Second-order consequence analysis of v22's own fixes + structural gap scan*
*Net new genuine flaws: 13 (10 G4-P priority + 3 G4-S structural)*
*Output: AEGIS_MASTER_PLAN_v23.md*
