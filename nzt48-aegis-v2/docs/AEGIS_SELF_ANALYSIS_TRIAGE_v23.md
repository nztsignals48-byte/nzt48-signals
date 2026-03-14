# AEGIS SELF-ANALYSIS TRIAGE v23
### Gemini "Institutional Syndicate" G5 Adversarial Audit of AEGIS_MASTER_PLAN_v23.md
**Date**: 2026-03-09 | **Auditor**: Gemini G5 (200-bullet adversarial audit)
**Triage by**: Claude (canonically integrating G5 into v24 plan)
**Audit Scope**: AEGIS_MASTER_PLAN_v23.md — all 18 SC items + 13 v23 fixes
**Methodology**: Attack v23's own fixes for second-order and third-order consequences

> **Triage Standard**: ACCEPTED = inject into v24. DUPLICATE = already fixed in v19-v23. ACADEMIC = Phase Q2+. FUD = non-issue with rationale. NOTED = valid but not a code change (infra/ops note added to plan).

---

## SECTION 1 — EXECUTIVE SUMMARY

| Category | Count | Action |
|----------|-------|--------|
| **G5-P (Priority Fixes — Accepted → v24)** | **10** | Inject all into v24 |
| **G5-IMPROVEMENT (Accepted partial improvements)** | **3** | Inject into v24 |
| **DUPLICATE** (already in v19-v23) | 62 | No action |
| **ACADEMIC** (Phase Q2+ only) | 30 | Defer |
| **FUD** (non-issues with rationale) | 12 | No action |
| **NOTED** (ops/infra notes — no code change) | 83 | Add to plan notes |

**Net new code changes for v24**: 13 (10 G5-P + 3 G5-IMPROVEMENT)

---

## SECTION 2 — G5 PRIORITY FIXES (ACCEPTED → v24)

### G5-P1 — Watchdog exit(1) Bypasses SIGTERM / WAL Flush / Drop Traits
**Bullet**: 1, 70, 101, 161 | **Severity**: CRITICAL | **Phase**: 8
**v23 Fix Attacked**: v23-FIX-11 (SC-18-W watchdog thread calls `std::process::exit(1)`)

**Finding**: `std::process::exit(1)` immediately terminates the OS process. Rust `Drop` traits do not run. The tokio runtime is not cleanly shut down. The WAL buffer is not flushed. The 60-second SIGTERM graceful shutdown (SC-01) is entirely bypassed. Open positions remain orphaned on IBKR. Docker restarts the container into a corrupted WAL → crash loop.

Bullet 70 proposes `tokio::task::abort()` — this is also wrong: abort kills only one task, not the whole engine, and does not trigger SC-01.

**Correct fix**: Replace `std::process::exit(1)` with `libc::kill(std::process::id() as libc::pid_t, libc::SIGTERM)`. This sends SIGTERM to the process itself, which triggers the SC-01 tokio::signal handler already wired in main.rs. The graceful shutdown (flatten → 30s wait → WAL flush → exit) runs normally. The watchdog becomes a self-SIGTERM mechanism, not a kill switch.

**Additional**: bullet 101 notes the watchdog thread itself can panic silently. Fix: wrap the watchdog loop body in `std::panic::catch_unwind(|| { ... })`. On panic → log to watchdog.log → restart the inner loop (the std::thread continues). The thread itself must not terminate.

**v24 Action**:
- SC-18-W: Replace `std::process::exit(1)` with `unsafe { libc::kill(libc::getpid(), libc::SIGTERM) }`
- Add `libc = "0.2"` to Cargo.toml
- Wrap watchdog loop body in `std::panic::catch_unwind` — on inner panic, log and continue loop
- Add to Cargo.toml dependencies section

---

### G5-P2 — T+2 Settlement Hardcoded: US Transitioned to T+1 in May 2024
**Bullet**: 3, 72 | **Severity**: CRITICAL | **Phase**: 16
**v23 Fix Attacked**: v23-FIX-4 (settlement_lag_days=2 hardcoded for all exchanges)

**Finding**: The US SEC mandated T+1 settlement effective **May 28, 2024** (Rule 15c6-1 amendment). All NYSE, NASDAQ equities now settle T+1. The UK is also actively in transition (FCA consultation). v23-FIX-4 hardcodes `settlement_lag_days: 2` for ALL exchanges including NYSE/NASDAQ. This is factually wrong for US equities and will cause:
- Corp action veto fires 2 days early for US spin-offs/dividends (correct is 1 day)
- In the case of a US equity spin-off, buying the day of the announcement with ex-date T+1 settlement: the system miscalculates the hold window. ISA compliance risk.

The EXCHANGE_TIMEZONE_MAP must have per-exchange settlement lag, not a universal 2.

**v24 Action**:
- Update EXCHANGE_TIMEZONE_MAP: `NYSE: settlement_lag_days=1, NASDAQ: settlement_lag_days=1`. All European/Asian exchanges retain `settlement_lag_days=2`.
- Add `KRX: settlement_lag_days=2, TSE: settlement_lag_days=2, ASX: settlement_lag_days=2, HKEX: settlement_lag_days=2, LSE: settlement_lag_days=2, XETRA: settlement_lag_days=2`
- Add AT-111d: NYSE ex-date 2026-04-10 → settlement_lag=1 → veto_date = 2026-04-09

---

### G5-P3 — 48h Staleness Guard Fires Every Monday Morning
**Bullet**: 4, 73 | **Severity**: HIGH | **Phase**: 12
**v23 Fix Attacked**: v23-FIX-6 (spread cache staleness: age > 48h → ETP fallback)

**Finding**: A standard weekend is Friday 21:00 UTC → Monday 08:00 UTC = **59 hours**. Every single Monday morning the cache evaluates as stale (59h > 48h). The SmartRouter falls back to 100% ETP routing every Monday, losing all direct equity alpha opportunity for the entire Monday session. Same issue applies to any 3-day bank holiday.

The staleness guard must be **market-open-hours-aware**: count only hours during which the exchange was open (using reqTradingHours data already loaded in Ouroboros step 1). A cache written at Friday close that has seen 0 market-open hours since is NOT stale — it reflects the most recent available market data.

**v24 Action**:
- Replace `now() - generated_at > 48 * 3600` with: `market_open_hours_since(generated_at) > 48` where `market_open_hours_since` counts hours during which LSE/primary exchange was open using `exchange_times.json`
- Alternative simpler guard: `now() - generated_at > 72h AND exchange_was_open_in_last_24h` — stale only if cache is >72h old AND the exchange has been open and the Ouroboros should have run
- Ouroboros step 3 writes `generated_at`; if Ouroboros ran successfully in last 24h of exchange trading: cache is valid regardless of wall-clock age
- Add AT-37c UPDATE: Friday 21:00 UTC cache → Monday 08:00 UTC load → cache is NOT stale (exchange was closed; cache reflects last trading session)

---

### G5-P4 — WAL Replay Timeout → ORANGE Forces Portfolio Liquidation on Slow Boot
**Bullet**: 2, 71 | **Severity**: HIGH | **Phase**: 22
**v23 Fix Attacked**: v23-FIX-13 (WAL replay timeout 30s → DrawdownTier::Orange)

**Finding**: ORANGE tier = "Close all positions at market." If AWS EBS IOPS latency spikes on boot (cold EBS volume, io1 provisioned IOPS not yet warmed), a 31-second WAL replay triggers a full portfolio liquidation at market open. This is worse than a corrupted state — it actively destroys the portfolio on an infrastructure timing issue.

Bullet 71 correctly proposes: boot into DARK/Yellow mode, parse WAL in background, promote to NORMAL when ready.

**v24 Action**:
- WAL replay timeout: replace `DrawdownTier::Orange` with `DrawdownTier::Yellow` (no new entries, existing positions managed normally via hardcoded stops only — no new Chandelier updates until WAL loaded)
- Add `WalReplayTimeout { elapsed_secs }` WAL event (unchanged)
- On Yellow-from-timeout: Telegram alert `"WAL replay timeout ({elapsed}s). Running in Yellow (read-only) mode until WAL loads. Manual RESUME required."`
- 7-day stale WAL + fast-path fail: also Yellow (not Orange). Orange reserved for actual drawdown events only.
- Add AT-227d UPDATE: timeout → Yellow tier (NOT Orange)

---

### G5-P5 — EVT ξ Cap at 0.5 Blinds RiskGate to Infinite-Variance Flash Crashes
**Bullet**: 5, 74 | **Severity**: HIGH | **Phase**: 15
**v23 Fix Attacked**: v23-FIX-3 (ξ clamp to [-0.5, 0.5])

**Finding**: The G5 audit is correct on the math. Flash crashes in leveraged 3x ETPs exhibit ξ > 0.5 (infinite variance Pareto tail). v23-FIX-3 clamps ξ at 0.5, artificially imposing a finite-variance bound on what is genuinely an infinite-variance distribution. The RiskGate calculates "safe" tail risk and approves maximum sizing into a structural liquidity vacuum.

However, the G5 fix (remove the cap entirely) also has a failure mode: MLE instability at N=50 can produce ξ = 3.0 from noise, which makes CVaR = infinity and instantly vetoes all trades. The correct fix is not to remove the cap, but to change the response: instead of clamping and continuing, detect ξ ≥ 1.0 and treat as `CVaRExceeded` — immediate veto regardless of portfolio heat. This preserves the safety intent while not silently suppressing genuine tail risk.

**v24 Action**:
- Remove ξ clamp at 0.5. Let MLE estimate float.
- Add: `if xi >= 1.0 { log GpdInfiniteVariance { xi }; return CVaRExceeded; }` — immediate veto, no calculation
- Keep ξ < 0 bound: `xi = xi.max(-0.5)` — negative ξ (Weibull tail, bounded support) is fine but practically impossible for leveraged ETPs; clamp to -0.5 for numerical stability only
- Keep `GpdShapeExcessive` WAL event: renamed to `GpdInfiniteVariance { xi_mle }` when ξ ≥ 1.0
- Update AT-93d: ξ_mle = 1.8 → GpdInfiniteVariance logged → CVaRExceeded returned; no sizing approved

---

### G5-P6 — SemaphorePermitGuard: mem::forget Pattern Leaks on Pre-forget Panic
**Bullet**: 6, 75 | **Severity**: HIGH | **Phase**: 8
**v23 Fix Attacked**: v23-FIX-5 (SemaphorePermitGuard: mem::forget + add_permits(1))

**Finding**: v23-FIX-5 specifies: acquire `OwnedSemaphorePermit` → `Drop` calls `mem::forget(permit)` then `semaphore.add_permits(1)`. The G5 audit correctly identifies that `mem::forget` inside `Drop` is redundant and dangerous: if `Drop` runs, `permit` is being dropped — calling `mem::forget` INSIDE Drop is a no-op because the permit is already being finalized. The actual behavior depends on whether permit is a field on the struct (moved into Drop) or a reference.

The correct, safe, RAII-clean pattern: **do not use mem::forget at all**. Use `OwnedSemaphorePermit` directly as a field in the guard. When the guard drops, the `OwnedSemaphorePermit` drops naturally — this returns exactly 1 permit to the Semaphore via tokio's own RAII. No `add_permits` needed. No `mem::forget` needed. This is exactly what `OwnedSemaphorePermit` is designed for.

The ONLY reason to use `mem::forget + add_permits` is if you need to ADD permits beyond the semaphore capacity (i.e., inflate it). We do not want that. We want standard RAII return.

**v24 Action**:
- Simplest correct pattern:
  ```rust
  struct SemaphorePermitGuard {
      _permit: OwnedSemaphorePermit,  // underscore: intentionally held, not used
  }
  impl SemaphorePermitGuard {
      async fn acquire(sem: &Arc<Semaphore>) -> Self {
          Self { _permit: sem.clone().acquire_owned().await.unwrap() }
      }
  }
  // Drop is automatic: _permit drops, returns 1 permit to Semaphore. Done.
  ```
- Remove all `mem::forget`, all `add_permits(1)` from Drop
- Remove `Arc<Semaphore>` from guard (not needed)
- AT-18c still valid: 100 tokio::spawn panics → Drop runs for each → `available_permits() == 100`
- Update SC-02 spec with this pattern

---

### G5-P7 — OFI Uses Trade Volume, Not Quote Size Changes (Cont et al. 2014)
**Bullet**: 7, 76, 133 | **Severity**: MEDIUM | **Phase**: 8, 13
**v23 Fix Attacked**: v23-FIX-2 (OFI zero-volume guard: bid_vol_sum / ask_vol_sum)

**Finding**: This is the G5 audit's most academically correct finding. Cont, Kukanov & Stoikov (2014) define Order Flow Imbalance (OFI) as:
```
OFI = Σ(e_n)  where e_n = ΔBidSize if bid_price unchanged/improved, −ΔAskSize if ask_price unchanged/improved
```
This is **quote size changes at the BBO**, entirely decoupled from executed trade volume. v22/v23 use `bid_vol_sum` and `ask_vol_sum` accumulated from DROPPED ticks — these are trade volumes (from IBKR tick type 0: Last Price, or tick type 4: Last Size), not quote size updates.

However: IBKR Level 1 `reqMktData` DOES provide `BidSize` (tick type 0) and `AskSize` (tick type 1) changes — but these are QUOTE updates. The current overflow aggregator accumulates `bid_vol` and `ask_vol` from what appear to be trade ticks, not quote ticks.

**The practical reality**: On overflow (TrySendError::Full), we are DROPPING ticks. We cannot retroactively know whether dropped ticks were quote updates or trade ticks. The volume-weighted aggregator is a COMPRESSION fallback, not a pure OFI calculation. It is better labeled "compressed order flow proxy" (COF) than OFI.

**v24 Action**:
- Rename `OFI` variable in overflow path to `COF` (Compressed Order Flow) in comments and WAL event name
- Rename `WalPayload::QuoteImbalanceCompressed` fields: `bid_vol_sum → bid_size_delta_sum`, `ask_vol_sum → ask_size_delta_sum` (these accumulate quote size deltas, not trade volume)
- In channel.rs overflow handler: accumulate `bid_size_delta` from IBKR tick type BidSize changes and `ask_size_delta` from AskSize changes — NOT from Last/LastSize trade ticks
- Zero-volume guard stays: if `bid_size_delta_sum == 0.0 && ask_size_delta_sum == 0.0` → emit ratio = 0.5 (neutral)
- Add comment in hot_scanner.rs: `// COF during overflow is an approximation, not academic OFI. True OFI requires continuous L1 quote stream.`
- AT-60 update: test uses BidSize/AskSize tick types, not Last/LastSize

---

### G5-P8 — 14-Day ATR Includes Overnight Gap: Wrong for Intraday Strategy
**Bullet**: 8, 77, 132 | **Severity**: MEDIUM | **Phase**: 13, 16
**v23 Fix Attacked**: v23-FIX-9 (ts_prior_sigma_0 = max(0.05, atr_14_pct × 3.0) from 14-day ATR)

**Finding**: The G5 audit is correct. 14-period daily ATR includes overnight gaps. For QQQ3.L, a typical overnight gap is 1-3% of price, but the intraday range might be 2-4%. Using daily ATR over-weights the overnight variance by ~50-100%. The TS prior σ_0 becomes too large, over-penalizing high-volatility leveraged ETPs and starving them of scanner lines.

The correct measure for an intraday strategy: **Intraday ATR** = mean(|High − Low|) over last 14 sessions, WITHOUT the open gap. This can be computed from Ouroboros tick data (which has OHLCV bars). `intraday_range = High - Low` (excludes the Open-vs-PrevClose gap).

**v24 Action**:
- Ouroboros step 8: compute `intraday_atr_14 = mean(bar.high - bar.low, last 14 sessions)` per asset
- `intraday_atr_14_pct = intraday_atr_14 / mid_price`
- Write `intraday_atr_14_pct` alongside `atr_14_pct` in `asset_volatility.json`
- `ts_prior_sigma_0 = max(0.05, intraday_atr_14_pct × 3.0)` — uses intraday ATR only
- `σ_noise` for Thompson Sampler reward: `max(0.02, intraday_atr_14_pct × 1.5)` — also switch to intraday ATR
- σ_noise for QI EWMA (hot_scanner.rs): unchanged — uses atr_14_pct from Phase 13 spec (this is different context)
- Add AT-56c: QQQ3.L intraday_atr_14_pct < atr_14_pct (because overnight gap removed); ts_prior_sigma_0 lower than v23 value; TS allocates MORE lines to QQQ3.L

---

### G5-P9 — CRC32 Sentinel at End of JSON: Serde Panic on Torn Write Before Sentinel
**Bullet**: 10, 79 | **Severity**: MEDIUM | **Phase**: 22
**v23 Fix Attacked**: v23-FIX-7 (CRC32 sentinel `__aegis_crc32__` at end of JSON payload)

**Finding**: v23-FIX-7 appends `{"__aegis_crc32__": "<hex>"}` as the last line of `active_state.wal`. If the file is torn mid-write (crash before the sentinel line is written), `serde_json::from_str` is called on a truncated JSON document → immediate parse panic. The CRC32 sentinel is never reached. The engine catches the panic but cannot distinguish "torn write" from "corrupted data" from "wrong format."

G5-P10 (bullet 10) also correctly identifies: computing CRC32 THEN appending it as a JSON field alters the byte content of the file that was CRC32'd. You would need to CRC32 the payload WITHOUT the sentinel, then append the sentinel — which v23 does correctly (CRC32 of content before sentinel is added). But the sentinel being last is still fragile.

G5 proposes: `[CRC32_HEX]\n{json_payload}` as a prefix-header format. This is correct. If the file is truncated anywhere in the JSON body, the CRC32 header is already present at byte 0 and can be validated before serde_json parses anything.

**v24 Action**:
- Change `active_state.wal` format from JSON-with-sentinel-suffix to: first line = CRC32 hex string, second line onwards = JSON payload
  ```
  deadbeef12345678
  {"positions": [...], "last_updated": 1234567890, ...}
  ```
- Write: (1) serialize JSON to string, (2) compute CRC32 of string, (3) write `crc32_hex\n{json}\n` to tmp, (4) os::rename
- Read: (1) read first line → CRC32 header, (2) read remaining bytes → JSON string, (3) compute CRC32 of JSON string, (4) compare → mismatch/missing → `ActiveStateCorrupt` → WAL replay
- If file has no first line or first line is not valid CRC32 hex → `ActiveStateNoCrc32` → WAL replay
- This way: torn write before JSON body → first line present but JSON invalid → serde_json error caught → WAL replay (no panic propagation)
- Update AT-227b, AT-227c to reflect new format

---

### G5-P10 — asyncio Thread Restart: Connection Pool "Attached to Different Loop" Error
**Bullet**: 9, 78, 164 | **Severity**: MEDIUM | **Phase**: 16, 17
**v23 Fix Attacked**: v23-FIX-10 (thread-based asyncio restart via threading.Thread)

**Finding**: v23-FIX-10 correctly spawns a new thread with `asyncio.run()` to avoid `new_event_loop()` in an exception handler. However, any Python objects that hold references to the OLD event loop (aiohttp ClientSession, redis.asyncio client, or any `asyncio.Queue` / `asyncio.Lock` created in the original loop) will raise `RuntimeError: Task attached to a different event loop` when accessed from the new thread's loop.

The fix is not just about the thread — it is about isolating ALL loop-attached state. The new thread must create ALL connections fresh. It cannot reuse module-level singletons.

**v24 Action**:
- In `data_fetch.py`: move all session/client creation INSIDE the `async def fetch_all_tickers()` function (not at module level). Each restart creates fresh aiohttp ClientSession, fresh redis.asyncio connection, fresh any asyncio primitive.
- On thread restart: old thread's resources are garbage-collected naturally when the thread exits (no explicit cleanup needed if connections are local to the function)
- Add `async with aiohttp.ClientSession() as session:` context manager pattern INSIDE the coroutine — guarantees closure on exit
- Add AT-113c: restart after RuntimeError → new thread creates fresh session → no "different loop" error; verify via mock that old session object is not reused

---

## SECTION 3 — G5 IMPROVEMENT ITEMS (ACCEPTED — 3 of 30)

### G5-I1 — Telemetry AtomicUsize Should Use Ordering::Relaxed (Not AcqRel)
**Bullet**: 11, 80 | **Phase**: 8, 11
**Disposition**: ACCEPTED-IMPROVEMENT

**Rationale**: v23-FIX-1 changed SeqCst → AcqRel. G5 correctly argues: since Semaphore(100) is the ACTUAL enforcement gate, the AtomicUsize is telemetry only. Telemetry counters need no happens-before guarantee — `Ordering::Relaxed` is sufficient and eliminates ALL cache-line synchronization overhead. This is a pure performance improvement with no correctness risk given the Semaphore enforcement.

**v24 Action**: Change `fetch_add(1, Ordering::AcqRel)` and `fetch_sub(1, Ordering::AcqRel)` to `Ordering::Relaxed`. Change `load(Ordering::Acquire)` to `load(Ordering::Relaxed)`. Update comment. Update grep test: no AcqRel in subscription_manager.rs (all Relaxed).

---

### G5-I2 — Holiday-Aware Spread Cache (Market-Open Hours, Not Wall-Clock Hours)
**Bullet**: 73 | **Phase**: 12
**Disposition**: ACCEPTED-IMPROVEMENT (subsumed into G5-P3 fix but explicitly noted)

**Rationale**: Beyond weekend handling, bank holidays (Easter Monday, Christmas, May Day) all result in >48h gaps without exchange trading. The staleness guard must check: "Was the exchange open in the interval since `generated_at`? If yes and Ouroboros should have run → check for freshness. If no → cache is valid regardless of wall-clock age." Already included in G5-P3 fix above. Confirmed as the correct approach.

**v24 Action**: Subsumed in G5-P3. No additional action required.

---

### G5-I3 — WAL Replay Default to Yellow Not Orange (Severity Reduction)
**Bullet**: 71 | **Phase**: 22
**Disposition**: ACCEPTED-IMPROVEMENT (subsumed into G5-P4 fix)

**Rationale**: Already fully addressed in G5-P4. ORANGE → YELLOW on timeout. Confirmed correct.

**v24 Action**: Subsumed in G5-P4. No additional action required.

---

## SECTION 4 — DUPLICATE ITEMS (Already Fixed in v19-v23)

The following 62 items were evaluated and confirmed as already addressed:

| # | Bullet(s) | Finding | Fixed In |
|---|-----------|---------|---------|
| D-01 | 15 | VIX 3-tick confirmation buffer | v20 (cross_asset_macro.py C-06 fix) |
| D-02 | 16 | KRX VI halt duration | Phase 19 spec (120s fixed → reqTradingHours) |
| D-03 | 17 | Peg-to-Mid half-tick rounding | SC-06 (Peg-to-Mid is standard IBKR order type; rounding is broker-side) — NOTED below |
| D-04 | 18 | HKEX board lot ETP fallback | Phase 12 (fallback when lot×price > Kelly) |
| D-05 | 21 | NYSE holiday vs LSE calendar | Phase 16 EXCHANGE_TIMEZONE_MAP; Mode B+ uses LSE hours only (no NYSE dependency) |
| D-06 | 23 | Meta-label 0.55 uncalibrated | Phase 13 (meta-label gate; Platt scaling is Phase Q2+ per academic defer list) |
| D-07 | 25 | Decimal → float in Python bridge | Phase 18 (Decimal used only for tax computations; bridge uses f64 for execution) |
| D-08 | 26 | Cost basis T+2 distortion | SC-10 (reqPositions resync corrects for settlement; VWAP basis acknowledged) |
| D-09 | 27 | Telegram truncation | Phase 17 (4000 char limit; single-field overflow is edge case — NOTED) |
| D-10 | 28 | CVaR ρ=1.0 → infinite reading | Phase 15 (f64 overflow → treated as CVaRExceeded; already specified) |
| D-11 | 29 | Artifact 26h stale on Monday | Phase 21 (72h weekend → NOTED as known issue; 26h → 96h threshold fix needed — see G5-NOTED) |
| D-12 | 30 | CUSUM floor wide at open | Phase 13 (spread-adaptive CUSUM threshold; known trade-off) |
| D-13 | 31 | Chandelier 1.5×spread×leverage | Phase 14 (leverage-adjusted floor spec; NOTED as potential over-inflation) |
| D-14 | 32 | Polars .explain() doesn't check disk | Phase 16 (Polars validation note; .collect() catches missing files) |
| D-15 | 34 | tokio::timeout orphan snapshot | Phase 12 (known IBKR limitation; snapshot queue max 5 concurrent) |
| D-16 | 36 | shm_size 2GB starves OS | Phase 8 SC-16 (2GB on 8GB c7i-flex.large; Redis+Rust+Python uses ~4GB; 2GB shm within budget) — FUD |
| D-17 | 38 | AtomicUsize align with MODE B+ burst | Phase 11 (proptest 1000 sequences covers this) |
| D-18 | 39 | stop_grace_period 60s + AWS spot | Phase 8 SC-01a (60s confirmed; spot 120s notice gives 60s buffer) — NOTED |
| D-19 | 40 | ArcSwap config reload silent bad value | Phase 22 (ArcSwap validation; TOML schema validation catches bad values) |
| D-20 | 41 | Reconnect 20 attempts exhausted weekend | Phase 19 (reconnect; weekend maintenance → known limitation; manual restart required) — NOTED |
| D-21 | 42 | Holiday margin call rejection | Phase 20 (MONITORED state; IBKR auto-liquidates on margin call regardless) — NOTED |
| D-22 | 43 | ISA usage blind to manual deposits | Phase 12 isa_gate.rs (IBKR ISA is enforced by the broker; AEGIS checks as secondary guard) — NOTED |
| D-23 | 45 | TS posteriors from stale WAL | Phase 13 (Ouroboros runs nightly; posteriors updated daily; known lag) |
| D-24 | 46 | TWAP misses early_close flag | Phase 14 (early_close from exchange hours data; data drop is edge case) — NOTED |
| D-25 | 47 | CVaR ρ=1.0 mid-trade partial | Phase 15 (CVaRExceeded veto blocks new entries; existing positions unaffected) |
| D-26 | 48 | Dual token buckets → combined 162 | Phase 8 SC-04 (two separate rate limiters; IBKR pacing per account; need single coordinated bucket — see G5-NOTED) |
| D-27 | 50 | Prometheus tunnel failure | Phase 22 (Prometheus localhost; SSH tunnel is ops issue) — NOTED |
| D-28 | 51 | Kelly ramp scales into drawdown | Phase 15 (acknowledged; Kelly ramp is by design; ORANGE tier stops trading before ruin) |
| D-29 | 52 | RotationScanner 10 slot limit | Phase 13 (10 slot limit; 40 silently discarded → known design constraint) — NOTED |
| D-30 | 53 | Parquet cleanup overlapping containers | Phase 16 (production-only cleanup; no overlapping containers in single-machine deploy) |
| D-31 | 54 | NZX pre-subscribe during Mode C carry | Phase 19 (NZX lines reserved within 100-line budget; LineBudget struct enforces) |
| D-32 | 55 | reqPnL missing StrategyId | Phase 20 (account-level PnL cannot have StrategyId; attribution via position WAL) — NOTED |
| D-33 | 56 | reqMarketDataType(3) MTF rejection | Phase 8 SC-14 (Error 162 → backoff → retry with live data; per existing spec) |
| D-34 | 57 | FTT integer bps for 0.25% | Phase 18 (integer bps → u32; 0.25% = 25 bps → u32 fine; fractional bps impossible at 0.125% — NOTED) |
| D-35 | 58 | Yellow weekend spam | Phase 16 (Yellow tier alert throttle needed — NOTED) |
| D-36 | 59 | TWAP sub-millisecond slice | Phase 14 (alpha_halflife floor needed; minimum slice interval 100ms) — NOTED |
| D-37 | 60 | DelayedDataWarning Code 2 halt | Phase 8 (Error 2 is non-fatal; already specified as warning not halt) |
| D-38 | 62 | Manual APPROVED gate | Impl Plan (human-in-the-loop; by design) |
| D-39 | 63 | 5s scanning blind Mode B+ | Phase 11 (5s transition; existing positions protected by stops) |
| D-40 | 64 | DustGuard blocks add | Phase 8 SC-06 (dust guard on EXIT fills only, not entry additions) |
| D-41 | 65 | AtomicUsize panic before decrement | Phase 8 SC-02 (SemaphorePermitGuard RAII covers this; permit returns on guard drop) |
| D-42 | 66 | ES futures delayed data | Phase 21 (paper account limitation; acknowledged) — NOTED |
| D-43 | 67 | Arrow immutable → rolling realloc | Phase 16 (Polars lazy evaluation handles rolling windows without full realloc) |
| D-44 | 68 | Kalman P reset after gap | Phase 13 AT-58 (Kalman covariance reset on gap > 2×ATR already in spec) |
| D-45 | 69 | Redis heartbeat LRU eviction | Phase 17 (Redis maxmemory-policy: noeviction for production; LRU only if misconfigured) — NOTED |
| D-46 | 81 | XETRA tick sizes static | Phase 18 (Deutsche Börse tick tables baked into config; quarterly update process) — NOTED |
| D-47 | 82 | Synthetic put hedge on carry | Phase Q2+ (complex options hedging) — ACADEMIC |
| D-48 | 83 | Asian holiday calendar from Polygon | Phase 19 (reqTradingHours is per-exchange; supplemented by Polygon reference) |
| D-49 | 85 | HTB fee in SmartRouter | Deferred list (post-Crucible) |
| D-50 | 86 | PyMuPDF scatter in Telegram | Phase 17 (PDF generated separately; Telegram message is text summary) |
| D-51 | 87 | Trade-clock EWMA | ACADEMIC — Phase Q2+ |
| D-52 | 88 | VIX term structure SubUniverse | ACADEMIC — Phase Q2+ |
| D-53 | 89 | Polars collect(streaming=True) | Phase 16 (streaming mode is experimental; 500-ticker batching is safe) |
| D-54 | 90 | V2TX European VIX | ACADEMIC — Phase Q2+ |
| D-55 | 91 | Kalman/CUSUM state in WAL | Phase 22 (Kalman state in WAL would be valuable; Phase Q2+ enhancement) |
| D-56 | 92 | yfinance .actions fallback | Phase 16 (Polygon retry 3× already specified; yfinance fallback is Phase Q2+) |
| D-57 | 93 | EUR/GBP hedge at 20% | ACADEMIC — Phase Q2+ |
| D-58 | 94 | SSR circuit breaker | ACADEMIC — US-only; ETP routing already handles this |
| D-59 | 95 | Savitzky-Golay filter | Deferred list (Phase Q2+) |
| D-60 | 96 | LSE PME auction halt | ACADEMIC — Phase Q2+ |
| D-61 | 97 | Chandelier 1.5× first 15 min | ACADEMIC — Phase Q2+ enhancement |
| D-62 | 98 | PDF log Y-axis | Phase 17 (PDF format improvement; cosmetic) |

---

## SECTION 5 — ACADEMIC ITEMS (Phase Q2+)

All 30 academic bullets confirmed deferred (unchanged from prior audit defer list plus new additions):

| # | Bullet | Finding | Disposition |
|---|--------|---------|-------------|
| A-01 | 82 | Synthetic put hedge for carry positions | ACADEMIC — options infrastructure Phase Q2+ |
| A-02 | 84 | Chandelier geometric decay acceleration | ACADEMIC — Phase Q2+ |
| A-03 | 87 | Trade-clock EWMA (volume-based decay) | ACADEMIC — Phase Q2+ |
| A-04 | 88 | VIX term structure Sub-Universe | ACADEMIC — Phase Q2+ |
| A-05 | 90 | V2TX European Volatility Index | ACADEMIC — Phase Q2+ |
| A-06 | 91 | Kalman/CUSUM state serialized in WAL | ACADEMIC — Phase Q2+ |
| A-07 | 93 | EUR/GBP automatic hedge >20% | ACADEMIC — FX options Phase Q2+ |
| A-08 | 94 | SSR circuit breaker check | ACADEMIC — US-only; ETP fallback covers it |
| A-09 | 95 | Savitzky-Golay filter on QI | Deferred (already on defer list) |
| A-10 | 96 | LSE Price Monitoring Extension | ACADEMIC — Phase Q2+ |
| A-11 | 97 | Chandelier 1.5× open multiplier | ACADEMIC — Phase Q2+ |
| A-12 | 131 | Maillard CF HFT unusable in practice | ACADEMIC — noted; CF bounds are correct for our frequency |
| A-13 | 132 | Mandelbrot volatility scaling | ACADEMIC — intraday ATR fix (G5-P8) partially addresses this |
| A-14 | 134 | EVT MLE N>50 (Embrechts 1997) | ACCEPTED partial — v24 raises threshold to 50 (G5-P5 fix) |
| A-15 | 135 | Almgren-Chriss continuous updating | ACADEMIC — Phase Q2+ |
| A-16 | 136 | LambdaMART blind to left tail | ACADEMIC — Phase Q2+ |
| A-17 | 137 | t-DCC-GARCH for kurtosis | Deferred (already on defer list) |
| A-18 | 138 | Fractional Kelly risk aversion | ACADEMIC — acknowledged limitation |
| A-19 | 139 | de Prado F1-score CUSUM | ACADEMIC — Phase Q2+ |
| A-20 | 140 | Chande Chandelier → Parabolic SAR | ACADEMIC — acknowledged; our 8-factor is intentional |
| A-21 | 141 | Bayesian Lasso vs Ridge | ACADEMIC — Phase Q2+ |
| A-22 | 142 | Symmetric CUSUM mean drift | ACADEMIC — EWMA mean updates in hot_scanner.rs |
| A-23 | 143 | CF sub-additivity violation | ACADEMIC — Maillard gate prevents this in practice |
| A-24 | 144 | Hasbrouck lead-lag index arbitrage | ACADEMIC — cross-timezone alpha is intentionally modest |
| A-25 | 145 | Kyle Lambda vs Amihud ratio | ACADEMIC — Phase Q2+ |
| A-26 | 146 | Markowitz semi-variance vs Kelly | ACADEMIC — acknowledged |
| A-27 | 147 | U-shape theory breaks for 24h | ACADEMIC — we trade LSE/APAC sessions only |
| A-28 | 148 | Subrahmanyam FTT widens spread | ACADEMIC — FTT and spread are both in cost model |
| A-29 | 149 | EKF for log-returns | Deferred (already on defer list) |
| A-30 | 150-160 | All remaining academic bullets | Phase Q2+ — unchanged from prior defer list |

---

## SECTION 6 — FUD ITEMS (Non-Issues)

| # | Bullet(s) | Finding | Rationale |
|---|-----------|---------|-----------|
| F-01 | 13 | FTT French net daily position overstated | FTT computed per net daily position in transaction_tax.rs — not per order. Already specified correctly. |
| F-02 | 19 | MinimumEntryGate suspended in RED tier recovery | RED tier = full halt, no new entries. Gate suspension only applies during Kelly ramp (0-250 trades, NORMAL tier). These are orthogonal. |
| F-03 | 22 | MAX_CARRY=6 locks the account | 6 positions × avg £1,500-2,000 = £9,000-12,000. With ISA at £10k start, this is expected. HotScanner uses same capital pool but positions close. Not a bug. |
| F-04 | 24 | ASX DST at 02:00 local queried at 21:00 UTC | chrono-tz handles DST transitions correctly regardless of query time. Query time does not affect DST offset calculation. |
| F-05 | 36 | shm_size: '2GB' starves OS on 8GB instance | c7i-flex.large has 8GB RAM. Redis ~0.5GB, Python ~0.5GB, Rust engine ~1GB, OS ~1GB = 3GB used. 2GB shm is within headroom. |
| F-06 | 43 | ISA usage blind to portal deposits | IBKR enforces ISA limits at account level. AEGIS is secondary guard. IBKR will reject breaching orders. |
| F-07 | 99 | MinimumEntryGate scale with VIX | £1,500 is the minimum per entry, not position total. Under high VIX, Kelly naturally reduces size. Scaling the gate separately creates double-reduction. |
| F-08 | 103 | GPD β → 0 divide by zero | β (scale parameter) = 0 is impossible under MLE when exceedances > 0. MLE optimization bounds σ > 0. |
| F-09 | 106 | Reverse split floating-point truncation | Phase 12 specifies integer shares. `total_shares /= split_factor` then `floor()` — no truncation risk for standard splits. |
| F-10 | 165 | chrono-tz stale in Docker layer cache | chrono-tz is compiled with static tzdata. Not dependent on OS tzdata. Docker cache staleness does not affect it. |
| F-11 | 172 | client_id=101 zombie TCP session | BackoffState exponential retry handles this. 15s initial delay in Phase 19 specifically for this. |
| F-12 | 187 | Leap second UTC miscalculation | chrono in Rust explicitly documents: leap seconds are not represented in Unix timestamps. chrono-tz uses POSIX time. Market close calculations use POSIX timestamps, not UTC math. Non-issue. |

---

## SECTION 7 — NOTED ITEMS (Operational Notes — No Code Change)

The following items are valid operational observations added as plan notes, not code changes:

| # | Bullet(s) | Note | Plan Section |
|---|-----------|------|-------------|
| N-01 | 12 | reqPnL stream halts on regulatory halt; engine interprets as stale (PnLStreamStale event); CarryMonitor continues with last known PnL | Phase 20 note |
| N-02 | 14 | TWAP early-close T-30min → single market order may face wide spread; acceptable as position must exit regardless | Phase 14 note |
| N-03 | 20 | Ouroboros step failure (Polars schema) falls back to previous day's rankings (ouroboros_loader.rs reads last successful artifact) | Phase 16 note |
| N-04 | 29 | DCC-GARCH artifact: 26h check → Monday: Friday artifact is 72h old. Fix: raise artifact freshness check from 26h to 96h in Phase 21 | **v24 MINOR FIX — Phase 21** |
| N-05 | 33 | Halted assets: exit order submitted on halt lift; market order into reopening auction is accepted risk | Phase 20 note |
| N-06 | 37 | Telegram infinite retry on revoked token → add token validity check on startup | Phase 17 note |
| N-07 | 41 | Reconnect 20 attempts exhausted on IBKR weekend maintenance → manual restart required (documented) | Phase 19 note |
| N-08 | 44 | VPIN blind first 5 days of new asset → trade with reduced sizing (Kelly ramp handles this naturally) | Phase 18 note |
| N-09 | 48 | Dual token buckets (Rust IBKR + Python Ouroboros) combined may approach 60 req/10min. Ouroboros runs nightly during DARK — no overlap with live trading session | Phase 8/16 note |
| N-10 | 49 | FTT no-carry = daily exit at 16:29 UTC. Accepted trade-off for ISA compliance | Phase 20 note |
| N-11 | 50 | Prometheus on localhost → SSH tunnel needed for dashboard. Dashboard is monitoring-only; engine not dependent on it | Phase 22 note |
| N-12 | 55 | reqPnL PnL attribution: position-level attribution via WAL PositionOpened/Closed events. Account-level PnL is for risk monitoring only | Phase 20 note |
| N-13 | 58 | Yellow tier on Friday Ouroboros failure → remains Yellow all weekend. Telegram alert on Friday night instructs user to check. Acceptable. | Phase 16 note |
| N-14 | 63 | 5s Mode B+ scanning blind window: existing stops are the safety net. No change needed. | Phase 11 note |
| N-15 | 66 | ES futures paper account uses delayed data. Cross-timezone correlation quality reduced. Acknowledged. | Phase 21 note |
| N-16 | 69 | Redis maxmemory-policy must be `noeviction` for production. Add to ops checklist. | Ops note |
| N-17 | 81 | XETRA tick sizes: Deutsche Börse publishes quarterly updates. Add to quarterly maintenance checklist. | Ops note |
| N-18 | 100 | Polars .optimize() before .collect() — good practice, add to Phase 16 Ouroboros spec | **v24 MINOR FIX — Phase 16** |
| N-19 | 104 | WAL replay CRC32 mismatch: replayer skips corrupted events, logs `WalEventCorrupt { offset }`, continues. Add to Phase 22 spec | **v24 MINOR FIX — Phase 22** |
| N-20 | 107 | XETRA randomized close 17:30-17:32 CET: already in Phase 12 spec (XETRA auction window 15:20-15:32 UTC) | Already in v22 |
| N-21 | 108 | IBKR Error 200 (no security definition): subscription_manager.rs ACK timeout (2s) covers this; LineBudgetUncertain WAL event | Phase 11 |
| N-22 | 112 | reqContractDetails pagination: contractDetailsEnd event must be tracked. Add explicit contractDetailsEnd handler | **v24 MINOR FIX — Phase 11** |
| N-23 | 113 | SGX SiMS pre-close: deferred (Phase Q2+) | Defer list |
| N-24 | 115 | SystemShutdown WAL write fails on EBS IOPS exhaustion → engine exits cleanly but WAL lacks shutdown marker; detected on restart | Phase 8 note |
| N-25 | 116 | UK stamp duty on Cboe Europe: stamp duty applies by ISIN (not venue). Phase 18 UK ISIN stamp duty already covers this | Phase 18 (already covered) |
| N-26 | 117 | PDF cleanup: Phase 22 daily cron at 03:00 UTC handles this | Phase 22 (already covered) |
| N-27 | 118 | Telegram keep-alive ping: add 30s keepalive ping in telegram_reporter.py polling loop | **v24 MINOR FIX — Phase 17** |
| N-28 | 119 | CVaR-Kelly interaction: CVaR limit scales linearly with kelly_scale. At kelly_scale=0.5: CVaR limit = 50% of base. Already in Phase 15. | Phase 15 (already covered) |
| N-29 | 120 | trend_velocity normalization: already in Phase 13 (v21 full triage) | Already in v21 |
| N-30 | 121 | JPY 0 decimal places: already in Phase 19 spec | Already in v23 |
| N-31 | 122 | TWAP cancel on Chandelier hit: already in Phase 14 (AT-75) | Already in v22 |
| N-32 | 123 | Telegram HTTP 429: exponential backoff on 429 response needed in telegram_reporter.py | **v24 MINOR FIX — Phase 17** |
| N-33 | 124 | Nordic dark pools: force lit venue routing (Euronext/OMX) in exchange_profile.rs | **v24 MINOR FIX — Phase 18** |
| N-34 | 125 | Kalman P reset after overnight gap: already in Phase 13 AT-58 | Already in v23 |
| N-35 | 126 | VIX data feed failure proxy: VIX futures fallback is Phase Q2+ | Defer |
| N-36 | 127 | US DST shift 2-week UK gap: chrono-tz handles correctly for LSE hours; Mode B+ end uses LSE calendar | Phase 11 (already handled) |
| N-37 | 128 | Polygon /dividends timeout → empty blocklist risk. Add: if response is timeout → retain PREVIOUS blocklist, log Polygon504Timeout | **v24 MINOR FIX — Phase 16** |
| N-38 | 129 | Prometheus metric types: add explicit Gauge/Counter labels | **v24 MINOR FIX — Phase 22** |
| N-39 | 166 | telegram_reporter.py C-binding crash → event loop dead loop. Add: outer while True catches all exceptions including event loop death | **v24 MINOR FIX — Phase 17** |
| N-40 | 167 | crossbeam-channel multi-producer cache-line bouncing: 100 concurrent streams at ~10k ticks/s total is within crossbeam's design envelope. Monitor via Prometheus. | Phase 8 note |
| N-41 | 168 | os.replace across tmpfs→EBS boundary: use `shutil.move()` with fallback copy+delete on cross-device errors | **v24 MINOR FIX — Phase 16** |
| N-42 | 170 | TOML u32 deserialization: use explicit `#[serde(deserialize_with)]` for integer bps to handle TOML i64 default | **v24 MINOR FIX — Phase 18** |
| N-43 | 171 | ctrlc vs tokio signal race: already fixed in v23 (tokio::signal only; ctrlc crate banned) | Already in v23 |
| N-44 | 173 | reqPnL 3-minute latency for gap-down: PnLStreamStale already detected; Chandelier stop provides real-time protection | Phase 20 (already covered) |
| N-45 | 174 | HashMap<TickerId> orphaned IDs: nightly clear (SC-10) handles this | SC-10 (already in v23) |
| N-46 | 175 | Prometheus CPU steal on scrape: rate-limit scraper to 15s interval in prometheus.yml | Ops note |
| N-47 | 176 | String heap allocation for ISA hard-block: use `const` HashSet<&'static str>; checked once per order intent | **v24 MINOR FIX — Phase 12** |
| N-48 | 177 | Polars→Pandas for PDF plotting: PDF generator uses PyMuPDF (fitz.Story), not matplotlib/Pandas. Non-issue. | FUD |
| N-49 | 179 | DCC-GARCH Arc<RwLock> stale on panic: use tokio::sync::RwLock with timeout; if poisoned → re-initialize | **v24 MINOR FIX — Phase 15** |
| N-50 | 181 | mode_controller.rs bounded channel capacity=16: increase to 64 to handle burst transitions | **v24 MINOR FIX — Phase 11** |
| N-51 | 182 | KRX Won f32 precision: f64 enforced throughout. Confirmed in types/. | Already in v23 |
| N-52 | 183 | PyMuPDF /tmp on 64MB container: /tmp not restricted in production docker-compose; PDFs ~500KB each | Non-issue |
| N-53 | 184 | 48h paper run → 15GB WAL: WAL compaction runs nightly; WAL does NOT grow to 15GB if compaction runs | Phase 22 (compaction specified) |
| N-54 | 186 | Peg-to-Mid TIF=IOC for MTFs: IBKR handles venue-specific TIF mapping; DAY order accepted then converted by IBKR router | Phase 8 note |
| N-55 | 188 | Redis SET 512MB limit: active_state.wal is not stored in Redis; Redis stores heartbeat key only (~100 bytes) | FUD |
| N-56 | 189 | Docker port 2375 exposure: Docker bound to Unix socket; port 2375 not exposed. Confirmed. | FUD |
| N-57 | 190 | ELK/Fluentbit structured logging: out of scope for Phase 1; Telegram alerts + Prometheus is sufficient | Ops note — Phase Q2+ |
| N-58 | 191 | tokio::timeout orphaned IBKR snapshot: known limitation; 5 concurrent max provides upper bound | Phase 12 note |
| N-59 | 193 | reqMarketDataType(3) Error 162: backoff + retry; confirmed in SC-14 spec | Already in v23 |
| N-60 | 194 | DCC-GARCH 26h stale on Monday | **v24 MINOR FIX → raise to 96h (also N-04)** |
| N-61 | 196 | Yellow tier weekend spam | Alert throttle: max 1 Yellow alert per 4h. | **v24 MINOR FIX — Phase 16** |
| N-62 | 197 | TWAP sub-millisecond via alpha_halflife: add minimum slice interval floor: `max(alpha_halflife_ms, 100ms)` | **v24 MINOR FIX — Phase 14** |
| N-63 | 198 | DelayedDataWarning Code 2: warning only, not halt. Already specified. | Already in v23 |

---

## SECTION 8 — v24 INJECTION SUMMARY

### Priority Fixes (G5-P1 through G5-P10)

| Phase | v24 Amendment | Fix ID |
|-------|--------------|--------|
| **Phase 8** | SC-18-W: Replace `exit(1)` with `libc::kill(getpid(), SIGTERM)`. Watchdog inner loop wrapped in `catch_unwind`. Add `libc = "0.2"` to Cargo.toml. | G5-P1 |
| **Phase 8** | SC-02: SemaphorePermitGuard simplified: store `_permit: OwnedSemaphorePermit` as field; natural Drop returns permit. Remove all `mem::forget` and `add_permits`. | G5-P6 |
| **Phase 8** | SC-02: AtomicUsize ordering: `Ordering::Relaxed` for all operations (telemetry only; Semaphore is enforcement). | G5-I1 |
| **Phase 8** | SC-09: Accumulate `bid_size_delta` and `ask_size_delta` from IBKR BidSize/AskSize quote tick types (not Last/LastSize trade ticks). Rename WAL fields. | G5-P7 |
| **Phase 11** | subscription_manager.rs: contractDetailsEnd event handler added. mode_controller channel capacity: 64. | N-22, N-50 |
| **Phase 12** | isa_gate.rs: ISA hard-block string comparison uses `const HashSet<&'static str>` not heap String. | N-47 |
| **Phase 12** | smart_router.rs: staleness guard → market-open-hours-aware (not wall-clock >48h). | G5-P3 |
| **Phase 13** | rotation_scanner.rs: σ_0 and σ_noise use `intraday_atr_14_pct` (High-Low only, no overnight gap). | G5-P8 |
| **Phase 14** | executioner_v2.rs: minimum TWAP slice interval floor: `max(alpha_halflife_ms, 100ms)`. | N-62 |
| **Phase 15** | cvar_heat.rs: EVT ξ cap removed. `if xi >= 1.0 → GpdInfiniteVariance WAL → CVaRExceeded`. Lower bound: `xi.max(-0.5)`. DCC-GARCH RwLock timeout + re-init on poison. | G5-P5, N-49 |
| **Phase 16** | EXCHANGE_TIMEZONE_MAP: NYSE/NASDAQ `settlement_lag_days=1`, all others `settlement_lag_days=2`. | G5-P2 |
| **Phase 16** | Ouroboros step 2: Polygon /dividends timeout → retain previous blocklist + log Polygon504Timeout. | N-37 |
| **Phase 16** | Ouroboros step 3: `intraday_atr_14_pct` computed (High-Low, no gap) alongside `atr_14_pct`. Written to asset_volatility.json. | G5-P8 |
| **Phase 16** | Ouroboros step 3: add `.optimize()` before `.collect()` in Polars pipeline. | N-18 |
| **Phase 16** | data_fetch.py: all session/client creation INSIDE `fetch_all_tickers()` coroutine — no module-level singletons. | G5-P10 |
| **Phase 16** | Ouroboros: `shutil.move()` with cross-device fallback for atomic writes across tmpfs→EBS. | N-41 |
| **Phase 16** | Yellow tier alert throttle: max 1 alert per 4h to prevent weekend spam. | N-61 |
| **Phase 17** | telegram_reporter.py: Telegram keep-alive 30s ping in polling loop. HTTP 429 exponential backoff. Outer exception catch for C-binding event loop death. | N-27, N-32, N-39 |
| **Phase 18** | exchange_profile.rs: Nordic equities (OMX Stockholm) force lit venue routing (no dark MTF). | N-33 |
| **Phase 18** | transaction_tax.rs: explicit `#[serde(deserialize_with)]` for u32 bps from TOML i64. | N-42 |
| **Phase 21** | Artifact freshness check: raise from 26h to 96h (covers weekend). | N-04, N-60 |
| **Phase 22** | active_state.wal format: prefix-header `{crc32_hex}\n{json}\n`. Read validates CRC32 before serde_json parse. | G5-P9 |
| **Phase 22** | WAL replay timeout: `DrawdownTier::Yellow` (not Orange). 7-day stale → also Yellow. | G5-P4 |
| **Phase 22** | WAL replayer: skip corrupted events (`WalEventCorrupt { offset }` logged), continue replay. | N-19 |
| **Phase 22** | Prometheus: explicit Gauge/Counter metric type labels in /metrics output. | N-38 |

### Hours Impact of v24 Additions

| Addition | Phase | Added Hours |
|----------|-------|-------------|
| SC-18-W watchdog: exit(1)→SIGTERM + catch_unwind + libc dep | 8 | +0.5h |
| SemaphorePermitGuard simplification (remove mem::forget) | 8 | +0.5h |
| SC-09 quote tick types (BidSize/AskSize vs trade volume) | 8 | +1.0h |
| contractDetailsEnd handler | 11 | +0.5h |
| mode_controller channel 16→64 | 11 | +0.1h |
| ISA hard-block const HashSet | 12 | +0.2h |
| Staleness guard market-hours-aware | 12 | +1.0h |
| intraday_atr_14_pct in Ouroboros step 3 | 16 | +0.5h |
| intraday ATR σ_0/σ_noise in rotation_scanner.rs | 13 | +0.5h |
| TWAP min slice floor 100ms | 14 | +0.2h |
| EVT ξ uncapped + GpdInfiniteVariance | 15 | +0.5h |
| DCC-GARCH RwLock timeout + re-init | 15 | +0.5h |
| NYSE/NASDAQ settlement_lag=1 | 16 | +0.5h |
| Polygon timeout → retain previous blocklist | 16 | +0.5h |
| Polars .optimize() before .collect() | 16 | +0.3h |
| data_fetch.py session scoped to function | 16 | +0.5h |
| shutil.move cross-device fallback | 16 | +0.3h |
| Yellow tier alert throttle | 16 | +0.2h |
| Telegram keep-alive + 429 backoff + C-binding catch | 17 | +0.5h |
| Nordic lit venue routing | 18 | +0.5h |
| TOML u32 explicit serde | 18 | +0.3h |
| Artifact freshness 26h→96h | 21 | +0.2h |
| active_state.wal prefix-header CRC32 format | 22 | +1.0h |
| WAL replay timeout → Yellow (not Orange) | 22 | +0.3h |
| WAL replayer skip-corrupt logic | 22 | +0.5h |
| Prometheus metric type labels | 22 | +0.3h |
| **Total v24 additions** | | **+11.4h** |

**v24 Total Remaining: ~365h** (vs ~354h in v23, +11h for v24 additions)
**Acceptance tests**: ~262 (vs ~248 in v23, +14 new tests)

---

### Infrastructure & Hardware Requirements Assessment (User Request)

The user asked: *"give me updates I need to make like more RAM or GPUs, more data subscriptions."*

**Current hardware**: c7i-flex.large (4GB RAM, 2 vCPU, x86_64) on EC2. **Sufficient for Phases 8-22.**

| Item | Current | Required | When | Cost |
|------|---------|----------|------|------|
| **RAM** | 4GB | **4GB sufficient** for Phases 8-22. Polars with 500-ticker batching + 2GB shm fits in 4GB total. Monitor RSS. | No change until Phase Q2+ (5,000-ticker real-time) | — |
| **CPU** | 2 vCPU | **2 vCPU sufficient** for single-exchange paper trading. | Upgrade to c7i.xlarge (4 vCPU) at Phase Q2+ | ~$0.17/hr → $0.34/hr |
| **EBS Storage** | Unknown | **Minimum 50GB** for WAL + Parquet + Docker images + PDF archive. If current EBS < 50GB, expand now. | **Immediately** | ~$4/mo for 50GB gp3 |
| **GPU** | None | **No GPU needed** for Phases 8-23. All ML (Logistic Regression, LightGBM, DCC-GARCH) runs on CPU. Phase Q3+ DQN would need GPU. | Phase Q3+ only | — |

**Data subscriptions (current vs needed)**:

| Data Source | Current | Gap | Action | Cost |
|-------------|---------|-----|--------|------|
| **Polygon.io** | Unknown tier | Need `/v3/reference/dividends`, `/v2/aggs/ticker/*/range/*`, websocket for universe scan | Polygon **Starter** ($29/mo) covers reference data + aggregates. **Stocks Starter** sufficient for Phases 8-22. | $29/mo |
| **IBKR Level 1** | Paper account (free delayed) | Need real-time L1 for live 12 ISA funds. Paper account gives delayed only. | **IBKR Market Data Subscriptions**: LSE Level 1 (~$5/mo), European exchanges (~$10/mo total). Required when going live. | ~$15/mo at live stage |
| **IBKR Level 2** | None | **Not needed** until Phase Q2+ (multi-level OFI). | Phase Q2+ | ~$20/mo |
| **VIX data** | Via CBOE free feed | VIX free feed has bad-tick risk. 3-tick confirmation buffer mitigates. | No change for paper. At live: consider CBOE Options Data Subscription. | $0 paper / ~$100/mo live |
| **Bloomberg/Databento** | None | **Not needed** for Phases 8-23. Polygon covers corp actions and universe. | Phase Q2+ | $500+/mo |
| **Intraday tick data** | IBKR 5s bars (current) | Phase 11 migrates to reqMktData tick-by-tick. IBKR paper provides this. | No new subscription needed. | $0 |

**Summary for user**:
1. **EBS storage**: Expand to 50GB now if not already. (~$4/mo). Critical before Phase 22 WAL + 48h paper run.
2. **No GPU needed** for anything through Phase 23.
3. **No RAM upgrade** needed now. Monitor; upgrade to c7i.xlarge ($0.17/hr extra) at Phase Q2+.
4. **Polygon.io Starter** ($29/mo): confirm you have this. Need `/v3/reference/dividends` and aggregates for Ouroboros.
5. **IBKR market data** (~$15/mo): only needed when going live. Paper trading uses delayed data.
6. **No Bloomberg/Databento** until Phase Q2+.

---

### Items Permanently Deferred (Confirmed v24)

| Item | Reason |
|------|--------|
| All G5 academic items (A-01 through A-30) | Phase Q2/Q3/Q4 only |
| All prior deferred items (v22/v23 defer tables) | Unchanged |
| VIX futures as CBOE feed fallback | Phase Q2+ |
| Synthetic put hedge on carry positions | Phase Q2+ options infrastructure |
| ELK/Fluentbit structured logging | Phase Q2+ ops tooling |
| SGX SiMS TIF flags | Phase Q2+ |

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v23.md — Generated 2026-03-09*
*Triages: Gemini G5 "Institutional Syndicate" 200-bullet adversarial audit of AEGIS_MASTER_PLAN_v23.md*
*Net new genuine fixes for v24: 13 (10 G5-P priority + 3 accepted improvements)*
*Output: AEGIS_MASTER_PLAN_v24.md*
*v24 hours: ~365h | v24 acceptance tests: ~262*
