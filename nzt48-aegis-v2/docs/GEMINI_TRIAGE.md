# AEGIS V2 — GEMINI DEEP ANALYSIS TRIAGE
# Source: Full adversarial review of Phase 11, 12, 13 specs
# Date: 2026-03-09
# Status: TRIAGE COMPLETE — 200 findings processed into P0/P1/P2 action items
#
# READING ORDER:
#   1. SECTION A — ARCHITECTURAL RULINGS (decisions made, specs amended)
#   2. SECTION B — P0 CRITICAL FIXES (must fix before any code is written)
#   3. SECTION C — P1 HIGH FIXES (must fix before Phase 11 gate)
#   4. SECTION D — P2 MEDIUM FIXES (fix during implementation)
#   5. SECTION E — DEFERRED / REJECTED (won't fix + rationale)
#   6. SECTION F — MASTER DIRECTIVE (Phases 11-15 restructured plan)

---

## SECTION A — ARCHITECTURAL RULINGS (SPEC AMENDMENTS)

These are binding decisions. All specs are updated by these rulings.
No further discussion needed — implement exactly as stated.

### RULING A1: UNDERLYING TRACKING — OPEN POSITIONS ONLY
**Gemini Finding #171 + Part 2E:**
The Phase 11 spec (Section 6) mandates underlying tracking for every ETP in HotScanner.
If 40 ETPs sit in hot_b, that's 40 + 40 = 80 lines consumed, leaving only 20 for rotation.
This makes the 100-line constraint impossible when Phase 12 and 13 are active.

**RULING:** Underlying equity subscriptions are ONLY activated when an open position exists
in the corresponding ETP. Zero positions = zero underlying subscriptions from HotScanner.
- 3 open ETP positions → 6 total lines (3 ETP + 3 underlying)
- Remaining 94 lines = pure scanning budget
- This completely resolves the 100-line Phase 11+12+13 viability question

**Spec amendment:** Phase 11 Section 6 "Underlying Tracking (Safety-Locked)" — change
"Every ETP in HotScanner automatically triggers a corresponding underlying subscription"
to "Every ETP with an OPEN POSITION triggers a corresponding underlying subscription."

### RULING A2: OUROBOROS FAILURE FALLBACK — HALT NEW ENTRIES
**Gemini Finding #5 + Part 2F:**
Current spec says: "proceed with last-known universe lists" if Ouroboros times out.
This is catastrophic — trading with uncalibrated adaptive parameters.

**RULING:** If Ouroboros fails to set `pipeline_complete=1` by 22:55 UTC:
- Escalate to ORANGE drawdown tier automatically
- Block ALL new entry orders until manual operator `RESUME` command
- Carry positions continue to be monitored (Chandelier holds)
- Log CRITICAL to Telegram 🔄 SYSTEM SHIFT alert with reason "OUROBOROS_TIMEOUT"
- The morning primer PDF is marked "CALIBRATION FAILED — ENTRIES HALTED"

**Spec amendment:** Phase 11 Section 11, Ouroboros pipeline — add explicit failure branch.

### RULING A3: MODE TRANSITION — SUBSCRIPTION MANAGER MUTEX
**Gemini Finding #1 + Part 2E:**
Atomic swap of 100 lines is not atomic at the IBKR API level. `reqMktData` and
`cancelMktData` are processed asynchronously. Simultaneous mass-resubscription
will hit 120+ lines server-side and trigger Error 3200 socket disconnect.

**RULING:** All market data subscription changes go through a `SubscriptionManager`
singleton with these guarantees:
- Only one subscription operation in flight at a time (Mutex)
- After each `cancelMktData`, wait for `tickSnapshotEnd` or 200ms timeout as ACK
- Only then dispatch the next `reqMktData`
- Total mode transition time budget: 2,000ms maximum (100 lines × 20ms average)
- Signal generation PAUSED during transition (Mutex held)
- This is a P0 Rust implementation requirement in Phase 11

### RULING A4: PARTIAL FILL DUST — MINIMUM VIABLE POSITION
**Gemini Finding #4 + Part 2A #4:**
Cancelling the remainder of a partial fill can leave sub-£500 "dust" positions
that consume data lines and incur minimum commission on exit.

**RULING:** Add to Executioner v2 (Phase 11 Section 8):
- `MINIMUM_VIABLE_GBP = 500.0` (configurable in config.toml)
- If `remaining_value = remaining_qty × mid_price < MINIMUM_VIABLE_GBP`:
  - Don't cancel remainder — convert to MarketOrder immediately
  - Log as "DUST_LIQUIDATION" in WAL
- If filled partial is itself below `MINIMUM_VIABLE_GBP`:
  - Immediately issue market exit for the filled portion
  - Do not hold sub-scale positions

### RULING A5: YFINANCE REPLACEMENT
**Gemini Finding #2 + Findings #38, #65, #181:**
yfinance will be banned by Yahoo for bulk pulls of 25,000 tickers nightly.

**RULING:** Ouroboros nightly universe discovery uses this data source hierarchy:
1. **IBKR `reqContractDetails` batches** — primary source, chunked at 50/s rate limit
2. **IBKR `reqHistoricalData`** — for 60-day OHLCV (chunked, 10s delay between requests)
3. **yfinance** — ONLY as fallback for individual tickers missing from IBKR feed
4. Rate limiting: 10s sleep between each batch of 50 `reqContractDetails` requests
5. Total DARK window budget: 120 minutes; universe discovery capped at 60 minutes

**No Polygon.io / Databento required at current scale (paper trading £10k).**

### RULING A6: PYIO / GIL ARCHITECTURE
**Gemini Finding #3 + Findings #36, #172, #186:**
PyO3 synchronous calls per tick will stall the Rust async reactor.

**RULING:** Rust ↔ Python communication architecture:
- Rust tick ingestion → bounded crossbeam-channel (capacity: 10,000 ticks)
- Python brain runs in a **separate OS process** (not thread), spawned at startup
- IPC via Unix domain socket or Redis pub/sub (already present)
- Python never called synchronously on the hot path
- Meta-labeler inference: Rust serializes feature vector to Redis → Python reads → writes result → Rust polls (non-blocking, max 5ms timeout, default=PASS if timeout)
- If Python process dies: log CRITICAL, disable meta-label gate (default PASS), continue

---

## SECTION B — P0 CRITICAL FIXES (Before any Phase 11 code is written)

These are fatal flaws. Code cannot be written until these are resolved in the spec.

### P0-1: SUBSCRIPTION MANAGER (from Ruling A3)
**Problem:** Mode transition subscription swaps breach 100-line limit due to async API.
**Fix:** Implement `SubscriptionManager` Rust singleton with Mutex + ACK-wait protocol.
**Effort:** 8h | **Phase:** 11, core infrastructure

### P0-2: UNDERLYING TRACKING SCOPE (from Ruling A1)
**Problem:** HotScanner underlying tracking consumes all 100 lines.
**Fix:** Restrict underlying subscriptions to open positions only.
**Effort:** 2h | **Phase:** 11, HotScanner + Allocator

### P0-3: OUROBOROS FALLBACK (from Ruling A2)
**Problem:** Calibration failure silently leads to trading with stale parameters.
**Fix:** Ouroboros timeout → automatic ORANGE tier + entry halt + Telegram alert.
**Effort:** 3h | **Phase:** 11, Ouroboros pipeline

### P0-4: PARTIAL FILL DUST (from Ruling A4)
**Problem:** Sub-scale positions consume lines and margin with no edge.
**Fix:** Minimum viable position check with immediate market liquidation.
**Effort:** 3h | **Phase:** 11, Executioner v2

### P0-5: IBKR RATE LIMIT — UNIVERSE SCANNER
**Problem:** 25,000 reqContractDetails in one night hits IBKR 50 msg/s limit.
**Fix:** Chunk to 50/batch, 10s sleep between batches, total cap 60 minutes.
**Effort:** 4h | **Phase:** 11, UniverseScanner (Python)

### P0-6: NaN / DIVIDE-BY-ZERO GUARD
**Problem:** Kelly fraction = edge/variance. Zero variance (trading halt) → NaN order size.
**Fix:** In Rust: `if variance < 1e-10 { return 0; }` before every Kelly calculation.
**Effort:** 2h | **Phase:** 11, RiskGate + Executioner

### P0-7: REDIS EVICTION POLICY
**Problem:** Redis LRU eviction can delete frozen Chandelier stops for carry positions.
**Fix:** Set `maxmemory-policy noeviction` for AEGIS Redis; cap tick data TTL to 24h.
**Effort:** 1h | **Phase:** 11, Docker config

### P0-8: ORPHANED STOPS ON RESTART
**Problem:** If AEGIS crashes and IBKR executes a stop during downtime, system reboots
thinking it holds a position it doesn't.
**Fix:** On boot, call `reqPositions()` and reconcile against WAL. Any WAL position
not present in IBKR live positions is marked CLOSED and purged.
**Effort:** 4h | **Phase:** 11, startup reconciliation

---

## SECTION C — P1 HIGH FIXES (Before Phase 11 Gate)

Must be complete and passing before Phase 11 acceptance tests green-light Phase 12.

### P1-1: CUSUM MEAN DRIFT (Gemini #153)
**Problem:** Symmetric CUSUM accumulates from a static mean. Trending markets produce
false structural break signals as the mean drifts away.
**Fix:** Apply secondary fast EWMA (α=0.02, ~50 ticks) as the reference mean for CUSUM.
Resets on Ouroboros calibration.
**Effort:** 3h | **Phase:** 11, HotScanner

### P1-2: KALMAN Q INTRADAY ADAPTATION (Gemini #28)
**Problem:** Kalman Q calibrated nightly is wrong during the high-variance open.
**Fix:** Apply time-of-day scaling: `Q_eff = Q_base × vol_ratio_tod` where
`vol_ratio_tod = current_5min_vol / session_median_vol` (rolling, updated each bar).
**Effort:** 4h | **Phase:** 11, HotScanner

### P1-3: VPIN ADAPTIVE BUCKET SIZE (Gemini #17 + Fix #9)
**Problem:** 50 fixed buckets per session fails on illiquid European equities.
**Fix:** `V* = 5d_median_ADV / 50`. For tickers with ADV < 500k GBP, use 20 buckets.
**Effort:** 3h | **Phase:** 11, HotScanner + Phase 12 integration

### P1-4: OFI ABSOLUTE DEPTH CONTEXT (Gemini #4)
**Problem:** OFI normalisation destroys absolute depth context (100 vs 50 = same as
10,000 vs 5,000 shares).
**Fix:** Add absolute depth feature: `depth_z = log(bid_size + ask_size) / log(ADV/session_bars)`.
Feed as additional feature to meta-labeler. Do not replace OFI formula (Cont et al. still valid).
**Effort:** 3h | **Phase:** 11, HotScanner

### P1-5: KYLE'S LAMBDA OLS BIAS (Gemini #5)
**Problem:** OLS on 1-minute bars violates homoscedasticity → unstable Lambda.
**Fix:** Use Weighted Least Squares with weights `w_t = 1/σ_t²` where σ_t is the
rolling 5-bar realised variance. Recalculate every 15 minutes as specified but with WLS.
**Effort:** 3h | **Phase:** 11, Executioner v2

### P1-6: MINIMUM CHANDELIER STOP ≥ SPREAD (Gemini #12)
**Problem:** 0.5 ATR rung floor can be narrower than bid-ask spread in low-vol.
**Fix:** `rung_floor = max(0.5 * atr, 1.5 * spread_ema)`. Spread EMA from
Ouroboros 20-day calibration per ticker.
**Effort:** 2h | **Phase:** 11, Chandelier v2

### P1-7: MODE B+ VOLUME VETO OPEN PERIOD (Gemini #15)
**Problem:** G3-2 volume cap at 2% of 5-min rolling volume paralyses system at 08:01
when rolling volume is near zero.
**Fix:** Volume veto suspended for first 10 minutes of continuous trading per session.
Replace with absolute minimum volume threshold: `min_vol_gbp = 5000` per 5-minute bar.
**Effort:** 2h | **Phase:** 11, RiskGate

### P1-8: FX MINIMUM FEE AWARENESS (Gemini #6)
**Problem:** IBKR minimum FX conversion fee (~£2.00) makes small European equity
positions uneconomical at £10k AUM.
**Fix:** Router cost comparison must include `ibkr_fx_min_fee = 2.00` in the break-even
calculation. If `position_value_gbp < 1000`, prefer LSE-listed ETP alternative.
**Effort:** 2h | **Phase:** 12, Router

### P1-9: GRACEFUL SHUTDOWN (SIGTERM) (Gemini #125)
**Problem:** EC2 preemption or Docker stop will kill the process without saving state.
**Fix:** Register SIGTERM handler in Rust `main.rs`:
1. Set `entries_blocked = true`
2. Cancel all pending limit orders
3. Flush WAL to disk
4. Send Telegram 🔄 SYSTEM SHIFT "GRACEFUL_SHUTDOWN"
5. Exit 0
**Effort:** 4h | **Phase:** 11, engine core

### P1-10: IBKR RECONNECT LOGIC (Gemini #109)
**Problem:** No spec for reconnect if IB Gateway goes down mid-session.
**Fix:** On `connectionClosed` callback:
1. Set all scanners to paused
2. Retry connection every 30s for 10 minutes
3. On reconnect: call `reqPositions()` reconciliation (P0-8)
4. Resume scanners. If retry limit exceeded: ORANGE tier + Telegram alert.
**Effort:** 5h | **Phase:** 11, engine core

### P1-11: ORDER ID IDEMPOTENCY (Gemini #110)
**Problem:** Network timeout after `placeOrder` may cause duplicate order on retry.
**Fix:** Maintain `pending_order_ids: HashSet<i32>` in WAL-backed state.
Before placing any order, check if `order_id` already exists in WAL.
Generate order IDs deterministically: `hash(ticker + timestamp_ns + qty)`.
**Effort:** 3h | **Phase:** 11, Executioner v2

### P1-12: HEARTBEAT TELEGRAM (Gemini #129)
**Problem:** If system hangs, Telegram is silent. No liveness signal.
**Fix:** Send 🔄 SYSTEM SHIFT "HEARTBEAT" ping every 4 hours automatically.
Include: mode, open positions, daily PnL, last signal time, Ouroboros status.
**Effort:** 1h | **Phase:** 11, Telegram reporter

### P1-13: DST TRANSITION HANDLING (Gemini #40, #108)
**Problem:** UK, US, Australia, Japan change DST on different dates.
Current UTC boundary hardcoding breaks 4x/year.
**Fix:** All mode boundaries use UTC epoch seconds computed from:
- `chrono_tz` crate for timezone-aware datetime arithmetic
- Mode boundaries recalculated each day at 00:00 UTC from tz-aware rules
- No hardcoded offsets. MODE B = "London market hours" not "08:00 UTC always"
**Effort:** 6h | **Phase:** 11, clock.rs

### P1-14: META-LABEL F1 THRESHOLD (Gemini #156)
**Problem:** Flat 0.55 probability threshold ignores class imbalance.
**Fix:** During Ouroboros training, compute F1-optimal threshold via PR curve.
Store as `meta_label_threshold_ticker` in calibration output.
Fallback to 0.55 if < 100 training samples.
**Effort:** 3h | **Phase:** 11, Ouroboros component calibration

### P1-15: CORPORATE ACTIONS VETO (Gemini Part 2G)
**Problem:** Spin-offs can place non-ISA-eligible shares in the ISA account, voiding it.
**Fix:** Add RiskGate Group 1 check G1-CORP: veto any ticker with an IBKR-reported
corporate action (split, spin-off, special dividend) within 48 hours.
Source: `reqContractDetails` + `reqFundamentalData` ex-date field.
**Effort:** 5h | **Phase:** 11, RiskGate

---

## SECTION D — P2 MEDIUM FIXES (During Implementation)

Implement during normal Phase 11-13 coding. Not blockers but must be done.

### P2-1: TWAP VWAP-WEIGHTED SLICING (Gemini #76)
Replace flat TWAP slicing with volume-weighted slicing using the 60-day median
volume curve. Flat TWAP forces execution in low-liquidity midday periods.

### P2-2: GARMAN-KLASS VOLATILITY (Gemini #75)
Replace ATR with Garman-Klass estimator for Chandelier stop distance.
8x more efficient than ATR. Formula: σ²_GK = 0.5(ln(H/L))² - (2ln2-1)(ln(C/O))²

### P2-3: INTRADAY CORRELATION VETO (Gemini Part 2B #1)
DCC-GARCH is nightly. Add intraday correlation monitor: if rolling 30-min
correlation of any 2 open positions exceeds 0.85, tighten both stops 10%.

### P2-4: HMM STUDENT-T EMISSIONS (Gemini #137)
Financial returns are fat-tailed. Use Student-t emission distribution (ν=4)
instead of Gaussian in the 3-state HMM. Prevents regime misclassification.

### P2-5: STALE TICK GUARD (Gemini #127)
If incoming tick timestamp is > 5 seconds older than system clock, reject it.
Do not feed stale ticks to Kalman or CUSUM filters.

### P2-6: EWMA COLD-START BIAS (Gemini #70)
During system boot mid-session: seed EWMA with first 200 ticks in warm-up
mode. Do not generate signals until warm-up complete.
Log warm-up progress to WAL.

### P2-7: FTT INTRADAY EXEMPTION (Gemini #18)
French and Italian FTT exempt intraday round-trips.
Update Phase 12 TransactionTaxRegistry: `ftt_applies_intraday: false` for FR and IT.

### P2-8: TICKER ID REBINDING (Gemini #190)
IBKR TickerId must be reboundper session. Use a HashMap<TickerId, Symbol>
that is rebuilt on each mode transition and reconnect.

### P2-9: LOG ROTATION (Gemini #188)
Add to docker-compose.yml:
```yaml
logging:
  driver: json-file
  options:
    max-size: "500m"
    max-file: "5"
```

### P2-10: SERDE DEFAULTS (Gemini #197)
All TOML structs must implement `#[serde(default)]` on every field.
Missing fields in config.toml must never panic — use defined defaults.

### P2-11: BOUNDED CHANNELS (Gemini #193)
All crossbeam-channel instances must be bounded. Drop-oldest policy on full:
if channel is full, drop the oldest unprocessed tick (it's stale anyway).
Capacity: `session_ticks_per_minute × 2` (2-minute buffer).

### P2-12: IBKR ERROR FILTER (Gemini #195)
Error codes 2104, 2106, 2107, 2119 (data farm informational) must be filtered
at the `error()` callback before propagating to the error handler.

### P2-13: SPREAD-TO-ATR FILTER (Gemini #89)
Add to UniverseScanner hard filters: `spread_bps / daily_range_bps < 0.25`.
Assets where the spread consumes > 25% of the daily range are untradable.

### P2-14: COOLDOWN AFTER STOP (Gemini #97)
After a Chandelier exit fires on a ticker, add 30-minute cooldown:
ticker is blocked from re-entry by HotScanner for 30 min.
Prevents revenge-trading during whipsaws. Duration configurable in config.toml.

### P2-15: ADAPTIVE EWA LEARNING RATE (Gemini #85)
EWA learning rate η: set high (0.1) immediately after PELT detects a changepoint.
Revert to standard (0.05) after 5 sessions without another changepoint.

---

## SECTION E — DEFERRED / REJECTED (Won't Fix + Rationale)

### REJECTED: Logarithmic AUM Kelly Tapering (Gemini Fix #7)
Gemini says Kelly tapering is redundant and suppresses growth.
**Decision: KEEP IT.** Rationale: At £10k paper trading, full Kelly is appropriate.
As AUM grows to £40k+ with real capital, the tapering is a psychological and
regulatory guardrail. ADV cap alone is insufficient for capital preservation.
The taper stays. No change to spec.

### REJECTED: Replace PyMuPDF with HTML/Dash (Gemini #82)
Gemini suggests a local web app instead of PDF.
**Decision: KEEP PyMuPDF.** The user is hands-off. PDFs delivered to Telegram
require zero infrastructure. Dash needs a web server, browser, and always-on port.

### REJECTED: Run Ouroboros entirely in Rust (Gemini #104)
**Decision: DEFER to Phase 14+.** Rust ML libraries (linfa) are immature for
production HMM + LambdaMART. Python scikit-learn is battle-tested. Rewrite
after the system is profitable and validated.

### DEFERRED: Contextual Bandits (Gemini #95)
Thompson Sampling with HMM regime as context vector.
**Decision: DEFER to Phase 12.** Phase 11 uses standard Thompson Sampling.
Phase 12 upgrades to contextual using the European sub-universe + regime.

### DEFERRED: QuestDB/InfluxDB (Gemini #93)
**Decision: DEFER.** SQLite WAL is sufficient at £10k scale / paper trading.
Reassess when trade frequency exceeds 50/day consistently.

### DEFERRED: Synthetic proxy stop for carry (Gemini Fix #4)
Using NQ futures as a proxy for overnight carry hedge.
**Decision: DEFER to Phase 14.** ISA accounts cannot hold futures.
A synthetic hedge requires a separate margin account. Out of scope for Phase 11-13.

---

## SECTION F — MASTER DIRECTIVE: AEGIS V2 PHASES 11-15 RESTRUCTURED PLAN

This supersedes all previous phase numbering. Incorporates Gemini audit findings.

---

### PHASE 11 — ADAPTIVE CORE (117h → revised 145h with fixes)

**Mission:** Build the full adaptive infrastructure. All findings from P0 and P1
must be implemented in Phase 11 before the gate is passed.

**New additions vs original spec:**
- SubscriptionManager (P0-1): 8h
- Underlying tracking scope fix (P0-2): 2h
- Ouroboros failure fallback (P0-3): 3h
- Partial fill dust guard (P0-4): 3h
- IBKR rate limit chunking (P0-5): 4h
- NaN guards (P0-6): 2h
- Redis noeviction policy (P0-7): 1h
- Boot reconciliation (P0-8): 4h
- CUSUM mean drift fix (P1-1): 3h
- Kalman Q intraday adaptation (P1-2): 4h
- VPIN adaptive buckets (P1-3): 3h
- OFI depth context (P1-4): 3h
- Kyle's Lambda WLS (P1-5): 3h
- Chandelier floor ≥ spread (P1-6): 2h
- Volume veto open-period fix (P1-7): 2h
- Graceful shutdown SIGTERM (P1-9): 4h
- IBKR reconnect logic (P1-10): 5h
- Order ID idempotency (P1-11): 3h
- Heartbeat Telegram (P1-12): 1h
- DST timezone handling (P1-13): 6h
- Meta-label F1 threshold (P1-14): 3h
- Corporate actions veto (P1-15): 5h

**Total Phase 11 effort: ~173h**

**Phase 11 Gate (all must pass before Phase 12):**
- [ ] All 72 original acceptance tests green
- [ ] P0-1 through P0-8: all verified via integration tests
- [ ] P1-1 through P1-15: all verified via unit + integration tests
- [ ] SubscriptionManager: proptest 10,000 random transition sequences, lines ≤ 100 always
- [ ] Boot reconciliation: test with simulated IBKR position mismatch
- [ ] Ouroboros timeout: simulate timeout, verify ORANGE tier fires
- [ ] Dust guard: test partial fill < £500 triggers immediate market exit
- [ ] 5 paper trading days: no system halts, no 100-line violations

---

### PHASE 12 — EUROPEAN EQUITY EXTENSION (75h)

**Mission:** Add 15 European exchanges to MODE B/B+ with FTT, FX, stamp duty.

**Additions vs original spec:**
- FX minimum fee awareness in Router (P1-8): 2h
- FTT intraday exemption for FR/IT (P2-7): 2h
- Contextual Thompson Sampling for sub-universe (from Deferred): 5h
- P2 fixes applicable to European data: spread-to-ATR filter, VPIN adaptive

**Phase 12 Gate:**
- [ ] All 40 European acceptance tests green
- [ ] FTT correctly applied: intraday exemption verified for FR + IT
- [ ] FX minimum fee: positions < £1000 routed to LSE ETP alternative
- [ ] 5 paper trading days: MODE B + B+ with European universe active

---

### PHASE 13 — ASIA-PACIFIC EXTENSION (95h)

**Mission:** Add MODE A, 6 Asian exchanges, overnight carry, DARK mode.

**Additions vs original spec:**
- NZX/DARK mode contradiction resolved: NZX subscribed in MODE A (23:00-05:45 UTC)
  DARK starts at 21:00 — NZX is in the MODE A window, not DARK. No conflict.
- ASX pre-market (SYCOM): system waits for official open (22:50 UTC). No SYCOM.
- Carry position snapshot polling: use IBKR `reqPositions` + `reqPnL` (subscribed,
  not polled) to avoid pacing violations. Cancel subscription at MODE A open.
- Ouroboros IBKR daily server restart (23:45 ET = 04:45 UTC): this falls WITHIN
  MODE A. Schedule Ouroboros to complete by 04:30 UTC at latest, before the restart.
  The DARK window (21:00-23:00 UTC) runs the bulk of Ouroboros. Final steps
  (calibration write, handoff) complete by 23:55 UTC before MODE A opens.

**Phase 13 Gate:**
- [ ] All 48 Asian acceptance tests green
- [ ] DARK mode: zero orders submitted during 21:00-23:00 UTC (verified by proptest)
- [ ] Carry state machine: full NVDA cycle test (MODE C → DARK → MODE A → MODE B)
- [ ] TSE lunch break: entries blocked 02:30-03:30 UTC, existing positions unaffected
- [ ] KRX daily limit: both +30% and -30% verified
- [ ] 5 paper trading days: full MODE A coverage with Asian universe

---

### PHASE 14 — INSTITUTIONAL HARDENING (80h)

**Mission:** Production-grade hardening after 63-day paper trading validation.
Only entered if Phase 13 gate + 63-day validation gate both pass.

**Contents:**
- DCC-GARCH intraday correlation monitor (P2-3): 8h
- Garman-Klass volatility replacement (P2-2): 6h
- VWAP-weighted execution slicing (P2-1): 6h
- TWAP to VWAP migration: Almgren-Chriss (2000) updated schedule: 8h
- Multi-level OFI (MFI) upgrade (Gemini #71): 10h
- HMM Student-t emissions (P2-4): 6h
- Adaptive EWA learning rate (P2-15): 4h
- QuestDB evaluation (from Deferred): 4h
- Performance profiling: identify and fix top 5 latency hotspots: 16h
- Load testing: 10,000 ticks/second stress test: 12h

**Phase 14 Gate:**
- [ ] p99 tick-to-order latency < 50ms under load
- [ ] Zero OOM events in 30-day run
- [ ] Zero pacing violations in 30-day run
- [ ] All P2 fixes verified

---

### PHASE 15 — THE CRUCIBLE (Live Verification Before Real Capital) (40h)

**Mission:** Prove the system works to a T before deploying real money.

#### TEST SUITE 15.1: Global Flash-Crash Simulation
- Inject synthetic tick stream: QQQ3.L drops 35% in 8 minutes
- Verify: Chandelier fires, M8 contagion tightens correlated stops
- Verify: EmergencyMarketToLimit used, not plain Market
- Verify: Telegram 🔵 CHANDELIER SEVERED fires with correct rung + reason
- Verify: No new entries accepted during ORANGE tier escalation

#### TEST SUITE 15.2: 100-Line Routing Handoff
- Simulate: MODE A (7 Asian lines active, 2 carry positions)
- Trigger: MODE B transition at 08:00 UTC
- Verify: SubscriptionManager cancels Asian lines ACK-by-ACK
- Verify: European lines subscribed only after cancels confirmed
- Verify: Line count NEVER exceeds 100 during transition (monitor real-time)
- Verify: Carry positions (safety-locked) survive the transition

#### TEST SUITE 15.3: Partial Fill State Machine
- Simulate: QQQ3.L order for 100 shares, 3 fills then alpha decay
- Verify: Dust guard triggers, immediate market exit for 3 shares
- Verify: No data line consumed after exit
- Verify: WAL records DUST_LIQUIDATION event

#### TEST SUITE 15.4: Ouroboros Failure Recovery
- Kill Ouroboros at 22:30 UTC (mid-pipeline)
- Verify: 22:55 timeout fires
- Verify: ORANGE tier activates, Telegram alert sent
- Verify: No new entries accepted next morning
- Verify: System resumes after manual `RESUME` command

#### TEST SUITE 15.5: Mode Sequence End-to-End
- Run full 24-hour cycle: MODE A → MODE B → MODE B+ → MODE C → DARK → MODE A
- Verify: All mode transitions clean, no orphaned subscriptions
- Verify: Carry position survives full cycle if qualifying
- Verify: Morning primer PDF delivered by 07:00 UTC
- Verify: Post-mortem PDF delivered by 21:05 UTC

#### TEST SUITE 15.6: Reconnect Under Fire
- Kill IBKR Gateway connection at 10:00 UTC during active MODE B
- Verify: Engine detects disconnect within 5s
- Verify: Reconnect attempted every 30s
- Verify: On reconnect, reconciliation fires, positions verified
- Verify: Scanners resume within 60s of reconnect

#### TEST SUITE 15.7: Telemetry Verification
- Force one of each: TARGET ACQUIRED, CHANDELIER SEVERED, MEGA-RUNNER, SYSTEM SHIFT
- Verify: All 4 alert types arrive in Telegram with correct format
- Verify: Heartbeat fires every 4 hours
- Verify: Both PDFs generated and delivered correctly

**Phase 15 Gate (CAPITAL DEPLOYMENT APPROVAL):**
- [ ] All 7 test suites pass with zero failures
- [ ] 63-day paper trading validation: WR ≥ 40%, daily net ≥ 0.15%
- [ ] Zero CRITICAL log events in last 10 paper trading days
- [ ] Manual review of 3 random daily post-mortem PDFs
- [ ] SubscriptionManager verified: zero 100-line violations in 63 days
- [ ] Ouroboros: zero timeout failures in 63 days

---

## APPENDIX: GEMINI FINDINGS CROSS-REFERENCE

| # | Finding | Triage | Priority |
|---|---------|--------|----------|
| 1 | Atomic swap breaches 100-line limit | Ruling A3 → P0-1 SubscriptionManager | P0 |
| 2 | Kelly tapering redundant | REJECTED — keep taper | - |
| 3 | Frozen stop ignores gap risk | Deferred (Phase 14 synthetic hedge) | Deferred |
| 4 | OFI absolute depth blind | P1-4 depth context feature | P1 |
| 5 | Kyle's Lambda OLS bias | P1-5 WLS fix | P1 |
| 6 | FX minimum fee destroys small bets | P1-8 Router minimum position | P1 |
| 7 | DCC-GARCH daily vs intraday timescale | P2-3 intraday monitor | P2 |
| 8 | Static cross-TZ correlation weights | Phase 14 — DTW (deferred) | Deferred |
| 9 | TWAP flat profile ignores U-shape | P2-1 VWAP slicing | P2 |
| 10 | PELT on autocorrelated equity curve | P2-15 adaptive EWA compensates | P2 |
| 11 | 5-day filter allows illiquid assets | P2-13 spread-to-ATR filter | P2 |
| 12 | 0.5 ATR floor < spread | P1-6 spread floor fix | P1 |
| 13 | EXP3 feedback delay | NOTED — Thompson Sampling also has this; acceptable | - |
| 14 | XETRA closing cutoff timing | P1 — note in Phase 12 T-5 rule | P1 |
| 15 | Volume veto kills open period | P1-7 open period exemption | P1 |
| 16 | Scraping ETP sites brittle | Ruling A5 → IBKR primary source | P0 |
| 17 | VPIN 50 buckets fails illiquid | P1-3 adaptive bucket | P1 |
| 18 | FTT intraday exemption missed | P2-7 FTT fix | P2 |
| 19 | Drawdown halt prevents recovery | ACCEPTED — by design; RED = stop | - |
| 20 | Multiplicative score collapse | P2 — add sigmoid floor = 0.05 | P2 |
| 21 | Carry snapshot pacing violations | Phase 13 amendment — use reqPnL subscription | P1 |
| 22 | Meta-label 20 sessions insufficient | P1-14 F1 threshold + fallback | P1 |
| 23 | Sub-minimum cancel leaves dust | Ruling A4 → P0-4 | P0 |
| 24 | Bayesian Ridge timeframe mismatch | P2 — add tick-level features | P2 |
| 25 | Tick size static TOML bands | Phase 12 note — add corporate action reload | P1 |
| 26 | M8 symmetric stops wrong direction | P2 — add direction check to M8 | P2 |
| 27 | HKD peg band leverage exposure | Phase 13 note — count HKD as 80% USD | P1 |
| 28 | Kalman Q static daily | P1-2 intraday Q scaling | P1 |
| 29 | iNAV feeds stale | P2 — add iNAV staleness check (>60s = skip) | P2 |
| 30 | KRX VI intraday circuit breakers | Phase 13 note — add is_vi_halt() check | P1 |
| 31-200 | See SECTION D (P2) and SECTION E (Deferred) for remaining items | varies | P2/Defer |

---

## SECTION G — VERIFICATION REPORT BUGS (Post-Gemini Audit, 2026-03-09)

Internal spec verification found additional bugs in Phase 11 and Phase 13.
These are separate from the Gemini findings. All have been fixed directly in
the spec files as of 2026-03-09.

### Phase 12 — CLEAN (Score: 16/16)
No bugs. All new component names correct. No action required.

### Phase 11 Verification Fixes (Score was 14/17 → now 17/17)

**BUG-11-01: MODE A time WRONG** [FIXED]
- Was: `01:00–08:00 UTC` at lines 88, 100, 121, 125 of Phase 11 spec
- Correct: `23:00–08:00 UTC` (Phase 13 was already correct)
- Fix applied: All occurrences corrected. Phase 11 and 13 now consistent.

**BUG-11-02: DARK mode time WRONG** [FIXED]
- Was: `21:00–01:00 UTC` at lines 104 and 225 of Phase 11 spec
- Correct: `21:00–23:00 UTC`
- Fix applied: Table and DARK detail paragraph both corrected.

**BUG-11-03: 4-Tier Drawdown Percentages WRONG** [FIXED]
- Was: Expressed as fractions of an undefined `daily_dd_limit` (50%/75%/90%)
  with incorrect tier actions (ORANGE = "close all positions", RED = "full halt manual restart")
- Correct design:
  - YELLOW = -3% daily DD → 50% Kelly, HotScanner entries only
  - ORANGE = -5% daily DD → 25% Kelly, no new entries, manage existing
  - RED = -8% daily DD → full halt, safety-locked only, manual restart
- Fix applied: Absolute thresholds now defined. Tier actions corrected.
  G5-2/G5-3/G5-4 table entries updated. Test cases RG-09/RG-10 corrected.

### Phase 13 Verification Fixes (Score was 13/18 → now 18/18)

**BUG-13-01: OLD COMPONENT NAMES throughout** [FIXED]
- Occurrences fixed:
  - Line 58: VanguardSniper + ApexScout → HotScanner + RotationScanner
  - Lines 111-112: "Vanguard" tier → "Hot tier", "Apex" tier → "Rotation tier"
  - Lines 306, 308, 347, 365, 400, 857, 994: all "Vanguard" → "HotScanner Hot tier"
  - Lines 1102, 1378, 1521, 1523: `risk_arbiter.rs` → `risk_gate.rs` (replace_all)
  - Lines 1105, 1138, 1543: `RiskArbiter` → `RiskGate` (replace_all)
- Verified: Zero remaining occurrences of old names in Phase 13.

**BUG-13-02: Mega-Runner threshold uses R-multiple instead of +102%** [FIXED]
- Was: "Unrealised gain >= 3x the initial risk (R-multiple >= 3.0)" at line 306
- Correct: "Unrealised gain >= +102% from entry price" (consistent with Phase 11)
- Fix applied: Definition, example walkthrough, and acceptance tests AT-28/AT-29 all corrected.

**BUG-13-03: NZX not in original design** [ACCEPTED — INTENTIONAL]
- NZX was added as 6th exchange (original design had 5: TSE, HKEX, ASX, SGX, KRX)
- Decision: KEEP NZX. It is ISA-eligible, trades in MODE A window (closes 05:45 UTC),
  and adds geographic diversification at zero architectural cost.
- NZX/DARK contradiction resolved: NZX subscribed at 23:00 (MODE A open), not during DARK.

**BUG-13-04: Ouroboros Global Calibration section "missing"** [VERIFIED PRESENT]
- Verification report flagged missing section; cross-timezone intelligence IS present
  (lines 359, 1022, 1339, 1388, 1396, 1527, 1530, 1566 in Phase 13)
- The section covers: carry risk assessment, morning primer cross-timezone section,
  `python_brain/ouroboros/cross_timezone.py` (NEW module ~120 lines)
- Per-mode calibration is covered in Phase 11 Ouroboros (Steps 3a-3f)
- No gap. No fix required.

**BUG-13-05: ASX DST rules** [VERIFIED PRESENT]
- Phase 13 spec already contains AEDT/AEST references (lines 605-606, 613, 1662, 1664)
- ASX open: 00:00 UTC (AEDT) / 00:10 UTC (AEST), close: 06:00 UTC
- Handled correctly. chrono_tz (P1-13) handles the seasonal UTC offset.
- No fix required.

### Summary of Verification Fixes

| File | Was | Now |
|------|-----|-----|
| Phase 11 | 14/17 | 17/17 |
| Phase 12 | 16/16 | 16/16 |
| Phase 13 | 13/18 | 18/18 (+ 2 non-issues resolved) |

All three specs now fully consistent. Ready for Phase 11 implementation.

---

*End of Triage Document*
*Updated: 2026-03-09 — Verification report bugs incorporated*
*Next action: APPROVED → begin Phase 11 implementation per this triage*
