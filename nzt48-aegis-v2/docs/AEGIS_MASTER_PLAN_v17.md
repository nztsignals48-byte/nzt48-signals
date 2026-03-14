# AEGIS V2 — MASTER PLAN v17
# Definitive Phased Build Roadmap: Phases 11–15
# ─────────────────────────────────────────────────────────────────────────────
# Authored: 2026-03-09
# Basis: Phases 1–10 codebase (36 Rust modules, 7 configs, 26 docs),
#        Phase 11/12/13 Specs (5,218 combined lines post-triage),
#        GEMINI_TRIAGE.md (675 lines, 200 bullets),
#        AEGIS_SELF_ANALYSIS_TRIAGE.md (763 lines, 140 bullets, 10 TZ decisions)
# Status: AWAITING APPROVAL — do not implement until "APPROVED"
# ─────────────────────────────────────────────────────────────────────────────

---

## PREAMBLE — WHAT THIS DOCUMENT IS

This is the single authoritative build plan for AEGIS V2 post-Phase 10. It
supersedes all previous master plan versions. It incorporates:

- Every binding ruling from GEMINI_TRIAGE.md (Sections A–G)
- Every P0/P1/P2 finding from AEGIS_SELF_ANALYSIS_TRIAGE.md
- All 10 DST/timezone decisions from the timezone decision matrix
- The complete Phase 11, 12, 13 specifications (post-verification, all bugs fixed)
- The V1 codebase adversarial audit (S15 strategy, chandelier, IBKR gateway, etc.)
- The 6 tasks of the Supreme Master Directive

**The implementation mandate:** Quality over brevity. Institutional-grade or nothing.
Every section below is a binding specification. No shortcuts. No stubs.

---

# TASK 1 — ARCHITECTURAL CLARIFICATION: THE 100-LINE BUDGET

## Ruling: The Scanner Conservation Law (Binding, All Phases)

The IBKR 100-line market data constraint is a hard physical limit enforced
by broker-side Error 3200. Violation corrupts the entire data feed.

**The Conservation Law:**

```
TOTAL LINES = CARRY_LINES + ACTIVE_POSITION_LINES + SCAN_LINES ≤ 100
```

where:

```
CARRY_LINES       = carry_count × 2         (ETP + underlying, safety-locked)
ACTIVE_LINES      = active_open_count × 2   (ETP + underlying, open position)
SCAN_LINES        = 100 - CARRY_LINES - ACTIVE_LINES
```

**The Underlying Tracking Rule (Absolute):**

Underlying equity tracking (e.g., NVDA for NVD3.L) is activated ONLY when
a live, filled position exists in the corresponding ETP. It is deactivated
the moment the position closes.

```
HotScanner scanning NVD3.L (no position) → 1 line (ETP only)
Open position in NVD3.L                  → 2 lines (ETP + NVDA underlying)
Position in NVD3.L closes               → 1 line cancelled (NVDA released)
```

**This is NOT:**
- Tracking underlyings for ETPs in the scanning queue
- Tracking underlyings for ETPs in the RotationScanner pool
- Tracking underlyings for ETPs being evaluated by HotScanner

**Worked Example (Worst Case, All Three Phases Active):**

```
Carry positions (6 max):     6 ETP + 6 underlying = 12 lines
Active open positions (3):   3 ETP + 3 underlying = 6 lines
Available for scanning:      100 - 12 - 6 = 82 lines
```

82 lines is sufficient for HotScanner (top 40) + RotationScanner (42 snapshots).

**Maximum Carry Positions: 6** (enforced by `MAX_CARRY_POSITIONS = 6` in
`overnight_carry.rs`). The 7th qualifying mega-runner is flattened at mode
close with a `CARRY_CAP_REACHED` WAL event and Telegram alert.

**Maximum Simultaneous Open Positions: 3** (existing canonical rule #16,
unchanged from Phases 1–10).

## The SubscriptionManager: Deterministic Mode Transition

The critical race condition at mode transitions (e.g., 14:30 UTC when 20 US
lines open as 20 LSE lines close) is solved by a Mutex-guarded SubscriptionManager.

```rust
// rust_core/src/subscription_manager.rs

pub struct SubscriptionManager {
    /// Active subscriptions: TickerId → SubscriptionState
    active: HashMap<TickerId, SubscriptionState>,
    /// Atomic counter: MUST never exceed 100
    line_count: Arc<AtomicU32>,
    /// Pending cancellations awaiting broker ACK
    pending_cancels: HashSet<TickerId>,
    /// Safety-locked lines (carry positions, active positions)
    safety_locked: HashSet<TickerId>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SubscriptionState {
    Active,
    CancelPending { cancel_sent_at: Instant },
    Confirmed,  // Data flow observed to have ceased
}

impl SubscriptionManager {
    /// Cancel then subscribe — SEQUENTIAL, never concurrent.
    /// Step 1: cancel old subscriptions, move to CancelPending
    /// Step 2: wait for data flow to cease (no tick for 2s) → Confirmed
    /// Step 3: ONLY THEN open new subscriptions
    /// Invariant: line_count NEVER exceeds 100 at any point
    pub async fn transition(
        &mut self,
        cancel: Vec<TickerId>,
        subscribe: Vec<(TickerId, Contract)>,
        broker: &dyn BrokerAdapter,
    ) -> Result<(), SubscriptionError> {
        // Phase 1: Cancel
        for tid in &cancel {
            if self.safety_locked.contains(tid) {
                return Err(SubscriptionError::CannotCancelSafetyLocked(*tid));
            }
            broker.cancel_mkt_data(*tid).await?;
            self.active.insert(*tid, SubscriptionState::CancelPending {
                cancel_sent_at: Instant::now(),
            });
        }

        // Phase 2: Wait for ACK (data cessation = no tick for 2s)
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if Instant::now() > deadline {
                return Err(SubscriptionError::CancelTimeout);
            }
            let all_confirmed = cancel.iter().all(|tid| {
                matches!(
                    self.active.get(tid),
                    Some(SubscriptionState::Confirmed)
                )
            });
            if all_confirmed { break; }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }

        // Phase 3: Remove cancelled, open new
        for tid in &cancel {
            self.active.remove(tid);
        }
        for (tid, contract) in subscribe {
            let new_count = self.line_count.fetch_add(1, Ordering::SeqCst) + 1;
            if new_count > 100 {
                self.line_count.fetch_sub(1, Ordering::SeqCst);
                panic!("INVARIANT VIOLATION: 100-line budget exceeded. \
                       This is a P0 build failure.");
            }
            broker.req_mkt_data(tid, contract).await?;
            self.active.insert(tid, SubscriptionState::Active);
        }
        Ok(())
    }
}
```

**Proptest requirement:** 10,000 random transition sequences must never produce
`line_count > 100`. This is a CI gate — build fails if invariant is ever violated.

---

# TASK 2 — BRUTAL AUDIT: PHASES 1–10 FINDINGS & FIXES

## Audit Overview

Phases 1–10 represent the completed Rust/Python infrastructure: WAL, RiskArbiter,
exit engine, broker adapter, universe routing, Kelly sizing, Ouroboros, and paper
bootstrap. The audit below identifies institutional failure modes requiring fixes
before Phase 11 code can begin.

## A. Mode Transition Race Conditions

**Failure:** At 14:30 UTC (MODE B → MODE B+), the system must unsubscribe 20 LSE
scan lines and subscribe 20 US pre-market lines. If `reqMktData` calls are issued
before `cancelMktData` ACKs arrive, the broker briefly sees 120 active lines,
triggering Error 3200 and killing the entire data feed.

**Fix:** SubscriptionManager (above, Task 1). Sequential two-phase commit:
cancel → wait-for-cessation → subscribe. Never concurrent. The Mutex ensures
only one transition can run at a time. Mode transitions must call
`SubscriptionManager::transition()` exclusively — no raw `reqMktData` calls
in mode controller logic.

**V1 root cause** (`ibkr_gateway.py`): `cancel_subscription()` calls
`cancelMktData()` and immediately decrements the counter without waiting for
data cessation. The counter may read 80 while 100 feeds are still running.

**Phase 11 fix:** Replace all raw `reqMktData`/`cancelMktData` calls with
`SubscriptionManager::transition()`. Remove raw counter arithmetic from all
call sites. Add proptest gate.

## B. PyO3 GIL Latency and Thread Blocking

**Failure:** V1 calls Python signal evaluation synchronously from Rust tick
handlers. During the US market open (14:30 UTC), IBKR delivers tick bursts
of 500+ ticks/second. If Python eval takes 50ms per call and GIL is held,
the Tokio reactor stalls for 50ms — missing all ticks arriving during that
window. Real losses occur from missed exits.

**Fix (already in V2 design):** The `crossbeam-channel` ring buffer decouples
Rust ingestion from Python evaluation:

```
IBKR ticks → Rust tick handler (async, non-blocking)
           → crossbeam bounded channel (capacity: 50,000)
           → Dedicated GIL thread (batch: 200 ticks or 10ms)
           → Python evaluation (isolated from Tokio reactor)
```

**Additional requirements for Phase 11:**

```rust
// In python_bridge.rs — enhanced for Phase 11 multi-mode signals
const MAX_BATCH_SIZE: usize = 200;
const MAX_BATCH_AGE_MS: u64 = 10;
const GIL_TIMEOUT_MS: u64 = 500;  // NEW: circuit breaker

pub fn evaluate_batch(
    ticks: Vec<MarketTick>,
    mode: TradingMode,  // NEW: mode context for strategy dispatch
) -> Result<Vec<OrderIntent>, BrainError> {
    // If Python call exceeds GIL_TIMEOUT_MS → log ERROR, return last signals
    // (fail-open for signals, fail-closed for risk)
}
```

**Channel backpressure thresholds (Phase 11 extension):**
- `> 40,000` → REDUCE regime (existing rule)
- `> 50,000` → drop oldest ticks, HALT (existing rule)
- NEW: `> 200ms GIL hold` → log WARNING, escalate to REDUCE
- NEW: `> 500ms GIL hold` → log ERROR, treat as data staleness

## C. Partial Fill Dust and Margin Lock

**Failure:** HotScanner places an order for £2,000 of QQQ3.L. IBKR partially
fills £340 worth (17% of intent) before alpha decays. The system cancels the
remainder but holds £340 in a "dust" position. £340 is:
  - Below the £500 dust guard threshold → should liquidate
  - But also below the £1,500 minimum viable threshold → should never have been
    attempted at full Kelly if signal degraded this quickly

**Two-tier fix (already in Phase 11 spec, binding here):**

```rust
// Tier 1: Pre-entry gate (in Kelly sizer, before any order submission)
const MINIMUM_ENTRY_GBP: f64 = 1500.0;

pub fn pre_entry_gate(kelly_gbp: f64) -> Option<f64> {
    if kelly_gbp < MINIMUM_ENTRY_GBP {
        None  // Skip trade — logged as BELOW_MIN_SIZE in WAL
    } else {
        Some(kelly_gbp)
    }
}

// Tier 2: Post-partial-fill dust guard (in FillState machine, after cancel)
const MINIMUM_VIABLE_GBP: f64 = 500.0;

pub fn post_cancel_check(filled_value_gbp: f64) -> PostCancelAction {
    if filled_value_gbp < MINIMUM_VIABLE_GBP {
        PostCancelAction::ImmediateMarketExit  // DUST_LIQUIDATION in WAL
    } else {
        PostCancelAction::Hold
    }
}
```

**Alpha decay trigger for cancel:** If `remaining_intent_confidence < 0.25 ×
original_confidence` AND the position is partially filled AND the alpha source
(CUSUM/OFI) has reversed, submit IOC cancel for the unfilled remainder. This
prevents sitting in a full-size limit order as the signal decays.

## D. Ouroboros Data Sourcing

**Failure (V1 confirmed):** `uk_isa/lse_registry.py` calls `yf.Ticker().history()`
in a loop over 25,000+ tickers. Yahoo Finance's unofficial API rate-limits at
~2,000 requests/hour per IP. The nightly pipeline is banned within 2 hours.

**Fix — IBKR primary, yfinance banned from hot path:**

```python
# python_brain/ouroboros/universe.py

# BANNED: yf.Ticker(t).history() in any loop
# REQUIRED: reqHistoricalData via IBKR (already connected for Ouroboros client_id=200)

def pull_historical_batch(
    tickers: list[str],
    ibkr_client: IBKRClient,
    bar_size: str = "1 day",
    duration: str = "30 D",
) -> dict[str, pd.DataFrame]:
    """
    Pull historical bars via IBKR reqHistoricalData.
    Hard limit: 6 simultaneous requests (IBKR rule).
    Rate limit: ≤60 requests per 10 minutes (IBKR rule).
    Implements token bucket: 6 slots, refills at 1/10s.
    On Error 162 (pacing): exponential backoff (10s, 20s, 40s, max 5 min).
    """
    results = {}
    semaphore = asyncio.Semaphore(6)  # Max concurrent IBKR historical requests
    request_times = deque(maxlen=60)  # Rolling 10-minute window

    async def fetch_one(ticker: str):
        async with semaphore:
            # Enforce 60 req / 10 min
            now = time.monotonic()
            if len(request_times) >= 60:
                oldest = request_times[0]
                if now - oldest < 600:
                    await asyncio.sleep(600 - (now - oldest))
            request_times.append(time.monotonic())
            data = await ibkr_client.req_historical_data(ticker, bar_size, duration)
            results[ticker] = data

    await asyncio.gather(*[fetch_one(t) for t in tickers])
    return results
```

**yfinance is permitted ONLY for:**
- Pre-market overnight price pulls (Ouroboros step 6, one call per ticker at 07:45)
- Fallback when IBKR historical data returns empty (graceful degradation)
- Must be wrapped in try/except with backoff; failure is non-fatal

**Universe crawl phasing (to fit within 2-hour DARK window):**
- Phase 11: ~200 US tickers — IBKR reqContractDetails, 25/min safe rate = 8 min
- Phase 12: ~500 European tickers — 25/min = 20 min
- Phase 13: ~300 Asian tickers — 25/min = 12 min
- Total crawl budget: ~40 min of 120-min DARK window. Safe margin: 80 min.

## E. Overnight Carry Gap Risk

**Failure:** Chandelier stop is frozen at its last computed level during the
CARRIED state (MODE C close → DARK → MODE A open). A gap-down of 20% on TSE
open (caused by overnight news) leaves the position with a stop at -5% from
the old close price. The stop triggers on the first tick but the fill occurs
at -20% market open.

**This risk is inherent and cannot be eliminated.** The carry mechanism
explicitly accepts overnight gap risk as the cost of mega-runner participation.
What CAN be controlled:

1. **The carry qualification bar is already high (+102% unrealised PnL, stop
   ≥ breakeven):** A position this far in profit has substantial buffer against
   a gap-down. A 20% gap-down on a position with +102% unrealised gain still
   leaves the position in profit relative to entry.

2. **Deferred market exit on stop breach during DARK:** If a reqPnL update
   shows price below frozen_stop during DARK, a `DeferredMarketExit` is queued.
   It executes on the FIRST tick of MODE A — at the market open price, not the
   gap-down price. This is the correct institutional behavior.

3. **Carry cap enforces concentration limit:** Maximum 6 carries = maximum 6
   positions exposed to overnight gap risk simultaneously.

4. **The HALTED state handles exchange circuit breakers:** If the gap-down
   triggers a circuit breaker, the position enters HALTED (not an infinite
   MONITORED loop).

**Additional fix — reqPnL-based gap alerting:**

```rust
// In overnight_carry.rs
impl CarryPosition {
    pub fn on_pnl_update(&mut self, pnl_update: PnLUpdate) -> Option<CarryAction> {
        self.last_known_price = pnl_update.price;
        if pnl_update.price < self.frozen_stop {
            // Stop breached during DARK or MONITORED — defer market exit
            return Some(CarryAction::QueueDeferredExit {
                reason: ExitReason::CarryStopBreached,
                estimated_loss_gbp: (self.frozen_stop - pnl_update.price)
                    * self.qty as f64,
            });
        }
        // NEW: Gap-down alert threshold — warn if price drops >10% from last known
        let drop_pct = (self.last_known_price - pnl_update.price)
            / self.last_known_price;
        if drop_pct > 0.10 {
            return Some(CarryAction::SendGapAlert {
                drop_pct,
                current_price: pnl_update.price,
                frozen_stop: self.frozen_stop,
            });
        }
        None
    }
}
```

---

# TASK 3 — ABSOLUTE ADAPTIVITY AND AUM SCALING

## The Core Principle: AUM Physics Must Be Mathematical, Not Arbitrary

As the ISA portfolio grows from £10k → £20k → £40k+, three things change:

1. **Position sizes grow** → market impact increases → Almgren-Chriss execution required
2. **Kelly fractions produce larger absolute positions** → ADV caps become binding
3. **Risk thresholds in absolute £ terms grow** → but as % of portfolio they must shrink

The system must handle all three automatically without config changes.

## A. ADV-Bounded Execution: The AUM-Scaling Mechanism

**The key insight:** Instead of redundant fractional Kelly tapering, ADV-based
execution naturally constrains position size as AUM grows. A £10k portfolio
placing a 20% Kelly bet = £2,000. A £40k portfolio placing the same 20% Kelly
bet = £8,000. Both face the same market — but a £8,000 order in an instrument
with £50k daily volume (QQQ3.L equivalent) is a 16% ADV trade. At £10k, it
was 4% ADV.

**Hard rule: No order may exceed 1% of 5-minute rolling volume.**

```rust
// rust_core/src/executioner_v2.rs

pub struct AdVExecutionGate {
    /// 5-minute rolling volume per ticker (updated from ticks)
    rolling_volume: HashMap<TickerId, VolumeWindow>,
    /// ADV cap: 1% of 5-minute rolling volume
    adv_cap_fraction: f64,  // = 0.01 (configurable in config.toml)
}

impl AdVExecutionGate {
    /// Returns the maximum allowed order size (in shares) given current volume.
    /// If kelly_shares > cap → TWAP/VWAP slice the remainder.
    pub fn apply(&self, ticker: TickerId, kelly_shares: u32) -> ExecutionPlan {
        let five_min_vol = self.rolling_volume
            .get(&ticker)
            .map(|w| w.total())
            .unwrap_or(1000);  // Conservative floor if no data

        let cap_shares = (five_min_vol as f64 * self.adv_cap_fraction) as u32;
        let cap_shares = cap_shares.max(1);  // Minimum 1 share

        if kelly_shares <= cap_shares {
            // Fits in one order — direct execution
            ExecutionPlan::Single { shares: kelly_shares }
        } else {
            // Slice across alpha half-life via TWAP
            let slices = (kelly_shares as f64 / cap_shares as f64).ceil() as u32;
            let alpha_halflife_secs = self.estimate_alpha_halflife(ticker);
            ExecutionPlan::Twap {
                total_shares: kelly_shares,
                slice_size: cap_shares,
                interval_secs: alpha_halflife_secs / slices,
                slices_remaining: slices,
            }
        }
    }
}

/// Alpha half-life estimation (configurable, Ouroboros-calibrated nightly)
/// Default: 300s (5 min) for HotScanner momentum signals
/// RotationScanner signals: 600s (10 min, slower mean reversion)
fn estimate_alpha_halflife(&self, ticker: TickerId) -> u32 {
    // Read from Redis: calibrated by Ouroboros IC decay analysis (alpha_decay.py)
    self.halflife_cache.get(&ticker).copied().unwrap_or(300)
}
```

**Practical AUM scaling example:**

| AUM | Kelly % | Kelly £ | ADV Cap (1%) | Action |
|-----|---------|---------|--------------|--------|
| £10,000 | 20% | £2,000 | No constraint | Direct |
| £25,000 | 20% | £5,000 | Binding at £3,000 | TWAP 2 slices |
| £50,000 | 20% | £10,000 | Binding at £3,000 | TWAP 4 slices |
| £100,000+ | 15%* | £15,000 | Binding at £3,000 | TWAP 5 slices |

*At £100k+, Kelly fraction tapers logarithmically via AUM scaling bands
(full Kelly at £10k → 35% Kelly at £100k+, see Section below).

## B. Dynamic Risk Ceilings: U-Shaped Intraday Spread Curve

**The observation (Jain & Joh 1988, Andersen & Bollerslev 1997):** Bid-ask
spreads follow a U-shaped intraday pattern: wide at open and close, tight at
midday and US overlap. Fixed spread veto thresholds apply inappropriately
tight gates at open/close and inappropriately loose gates at midday.

**Fix: Time-of-day spread multiplier (already in V1 S15, now canonical):**

```rust
// rust_core/src/risk_gate.rs — G-16 enhanced for all modes

pub struct SpreadVetoGate {
    /// Base spread threshold (fraction of mid-price), Ouroboros-calibrated
    base_threshold: f64,
    /// Time-of-day multiplier lookup
    tod_multipliers: HashMap<TodBucket, f64>,
    /// VIX-regime multiplier
    vix_multipliers: Vec<(f64, f64)>,  // (vix_ceiling, multiplier)
}

#[derive(Hash, Eq, PartialEq)]
pub enum TodBucket {
    Open,        // 09:00–09:15 (LSE) / 00:00–00:10 (ASX) — auction spillover
    Morning,     // 09:15–11:30 — normal session
    Lunch,       // 11:30–13:00 — reduced liquidity
    Afternoon,   // 13:00–14:30 — normal session
    UsOverlap,   // 14:30–15:15 — peak institutional volume, TIGHTEST
    PreClose,    // 15:15–16:30 — EOD mechanics, wider
    Close,       // 16:30–16:35 — auction
}

impl SpreadVetoGate {
    pub fn effective_threshold(&self, now_utc: u32, vix: f64) -> f64 {
        let tod_mult = self.tod_multiplier(now_utc);
        let vix_mult = self.vix_multiplier(vix);
        self.base_threshold * tod_mult * vix_mult
    }
}
```

**Default multiplier values (Ouroboros-calibrated, not hardcoded):**

| ToD Bucket | Multiplier | Rationale |
|-----------|-----------|-----------|
| Open | 1.50 | Auction spillover, spreads structurally wide |
| Morning | 1.00 | Baseline |
| Lunch | 1.20 | Reduced book depth |
| Afternoon | 1.00 | Baseline |
| US Overlap | 0.80 | Peak liquidity — require TIGHTER spread |
| Pre-Close | 1.30 | EOD mechanics, MMs pulling |
| Close | 2.00 | Auction — no intraday trading allowed here |

## C. Infinite Chandelier: 8 Adaptive Multipliers

The Infinite Chandelier (Phase 11 spec, Section 9) replaces the fixed 5-rung
ladder with a geometric decay ladder of infinite depth. The 8 multiplier
dimensions are:

```
M_effective = M_base × f₁(ToD) × f₂(Regime) × f₃(Profit_Scale)
              × f₄(Momentum_Decay) × f₅(ATR_Percentile)
              × f₆(MAE_Calibration) × f₇(Vol_Regime) × f₈(Session)
```

| Multiplier | Domain | Effect |
|-----------|--------|--------|
| f₁ ToD | Open: +30%, Close: +20%, US Overlap: -15% | Wider stops during high-vol periods |
| f₂ Regime | BEAR_VOLATILE: +40%, BULL_QUIET: -10% | HMM regime gates |
| f₃ Profit Scale | +50%→100%: +20%, >+100%: +40% | Mega-runners get wider stops |
| f₄ Momentum Decay | IC < 0.3: +25% (less conviction) | Protect profit when signal weakens |
| f₅ ATR Percentile | ATR > 90th pct: +30% (extreme vol) | Outlier vol handling |
| f₆ MAE Calibration | Nightly Ouroboros from realized MAE | Empirically derived floor |
| f₇ Vol Regime | EWMA vol spike +50%: +20% | Intraday vol shock adjustment |
| f₈ Session | MODE A Asian: +15% (liquidity discount) | Illiquidity premium |

**Base multiplier decay:** M₁=3.0, M₂=2.55, M₃=2.17, M₄=1.84, ...,
Mₙ = 3.0 × 0.85^(n-1). No ceiling — the ladder is infinite.
Stop ONLY moves up (ratchet invariant). Stop NEVER decreases.

## D. CVaR Portfolio Heat: HMM-Regime Floating Limits

```rust
// rust_core/src/risk_gate.rs — G-CVaR

pub struct CvarHeatGate {
    /// Base heat limit (% of portfolio at risk from open positions)
    base_heat_limit: f64,  // 6.0% — canonical rule
}

impl CvarHeatGate {
    /// Returns the effective heat limit given current macro regime and VIX.
    /// Heat limit FLOATS inversely with risk environment.
    pub fn effective_limit(&self, regime: HmmRegime, vix: f64) -> f64 {
        let regime_mult = match regime {
            HmmRegime::BullQuiet     => 1.00,   // Full heat budget
            HmmRegime::BullVolatile  => 0.75,   // 25% reduction
            HmmRegime::BearQuiet     => 0.60,   // 40% reduction
            HmmRegime::BearVolatile  => 0.40,   // 60% reduction
        };
        let vix_mult = if vix < 15.0 { 1.00 }
            else if vix < 25.0 { 0.85 }
            else if vix < 35.0 { 0.65 }
            else { 0.45 };  // Extreme vol — severely constrained

        self.base_heat_limit * regime_mult * vix_mult
    }
}
```

**Result:** In BEAR_VOLATILE with VIX=40: heat limit = 6% × 0.40 × 0.45 = 1.08%.
The system can hold a maximum of ~1% of portfolio at risk simultaneously — barely
above cash. This is correct institutional behaviour during crises.

## E. AUM Scaling Bands (Logarithmic Kelly Taper)

The AUM scaling is a logarithmic function, not arbitrary bands:

```python
# python_brain/sizing/kelly_13factor.py

def aum_scaling_fraction(current_aum_gbp: float) -> float:
    """
    Logarithmic Kelly fraction scaling from £10k (100%) to £100k+ (35%).

    Derived from: f(AUM) = 1.0 - 0.65 × log10(AUM/10_000) / log10(10)
    At £10k:  f = 1.00 (100% Kelly)
    At £31k:  f = 0.75 (75% Kelly)
    At £100k: f = 0.35 (35% Kelly)
    At £1M+:  f = 0.04 (4% Kelly — near-zero, safety floor 0.05)

    Academic basis: MacLean, Thorp, Ziemba (2010) — Kelly scaling under
    parameter uncertainty. Fraction should decrease as position sizes become
    meaningful relative to market liquidity.
    """
    import math
    if current_aum_gbp <= 10_000:
        return 1.0
    log_ratio = math.log10(current_aum_gbp / 10_000) / math.log10(10)
    fraction = 1.0 - 0.65 * log_ratio
    return max(0.05, min(1.0, fraction))  # Floor: 5%, Ceiling: 100%
```

---

# TASK 4 — THE OMNI-PANOPTICON TELEMETRY STACK

## Design Principles

- **Low spam, high signal:** Never send a message the user cannot act on.
- **Async only:** `python-telegram-bot` async throughout. No blocking calls.
- **PDF via PyMuPDF only:** `fitz.Story` → `fitz.DocumentWriter`. No WeasyPrint,
  no wkhtmltopdf, no Jinja, no system dependencies.
- **4 Telegram message types only:** TARGET ACQUIRED, CHANDELIER SEVERED,
  MEGA-RUNNER CARRY, SYSTEM SHIFT.
- **RiskGate vetoes go to WAL and PDF only — NOT Telegram.** Vetoes are normal
  system operation, not actionable intelligence.

## A. Telegram Real-Time Pager

```python
# python_brain/ouroboros/telegram_reporter.py

import asyncio
from telegram import Bot
from telegram.constants import ParseMode

class AegisTelegramReporter:
    """
    Low-spam institutional pager. 4 message types only.
    Rate limit: Max 1 message per 10 seconds (Telegram API limit).
    """

    def __init__(self, bot_token: str, chat_id: str):
        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id
        self._last_sent_at: float = 0.0

    async def send_target_acquired(
        self,
        mode: str,
        ticker: str,
        source: str,           # e.g. "OFI + CUSUM Breakout"
        vehicle: str,          # e.g. "ETP: QQQ3.L" or "Direct: AAPL"
        fill_price: float,
        slippage_bps: float,
        allocation_pct: float,
        atr_mult: str,         # e.g. "M1 (3.0×)"
        portfolio_heat_pct: float,
        regime: str,
    ) -> None:
        """🟢 TARGET ACQUIRED"""
        msg = (
            f"🟢 *TARGET ACQUIRED*\n"
            f"`Mode:` {mode}\n"
            f"`Ticker:` {ticker}\n"
            f"`Signal:` {source}\n"
            f"`Vehicle:` {vehicle}\n"
            f"`Fill:` £{fill_price:.4f} | Slip: {slippage_bps:.1f}bps\n"
            f"`Size:` {allocation_pct:.1f}% equity\n"
            f"`Stop:` {atr_mult} ATR\n"
            f"`Heat:` {portfolio_heat_pct:.1f}% | `Regime:` {regime}"
        )
        await self._send(msg)

    async def send_chandelier_severed(
        self,
        mode: str,
        ticker: str,
        exit_price: float,
        net_pnl_pct: float,
        time_in_trade_mins: int,
        exit_mechanic: str,   # e.g. "M7 Volume Exhaustion" or "RiskGate RED"
    ) -> None:
        """🔵 CHANDELIER SEVERED"""
        pnl_sign = "+" if net_pnl_pct >= 0 else ""
        emoji = "🔵" if net_pnl_pct >= 0 else "🔴"
        msg = (
            f"{emoji} *CHANDELIER SEVERED*\n"
            f"`Mode:` {mode} | `Ticker:` {ticker}\n"
            f"`Exit:` £{exit_price:.4f}\n"
            f"`PnL:` {pnl_sign}{net_pnl_pct:.2f}%\n"
            f"`Hold:` {time_in_trade_mins}min\n"
            f"`Trigger:` {exit_mechanic}"
        )
        await self._send(msg)

    async def send_mega_runner_carry(
        self,
        ticker: str,
        unrealised_pct: float,
        profit_harvested_pct: float,
        remainder_pct: float,
        carry_mode: str,      # e.g. "MODE C → DARK → MODE A (TSE)"
        frozen_stop_pct: float,
    ) -> None:
        """🌟 MEGA-RUNNER CARRY"""
        msg = (
            f"🌟 *MEGA-RUNNER CARRY*\n"
            f"`Ticker:` {ticker}\n"
            f"`Unrealised:` +{unrealised_pct:.1f}%\n"
            f"`Harvested:` {profit_harvested_pct:.1f}% (T-5 rule)\n"
            f"`Carrying:` {remainder_pct:.1f}% position\n"
            f"`Route:` {carry_mode}\n"
            f"`Frozen Stop:` {frozen_stop_pct:.1f}% below current"
        )
        await self._send(msg)

    async def send_system_shift(
        self,
        shift_type: str,      # "HMM_REGIME" or "DRAWDOWN_TIER"
        from_state: str,
        to_state: str,
        trigger: str,         # e.g. "VIX 18→32 spike" or "-5% daily loss"
        heat_limit_new_pct: float,
    ) -> None:
        """🔄 SYSTEM SHIFT"""
        msg = (
            f"🔄 *SYSTEM SHIFT*\n"
            f"`Type:` {shift_type}\n"
            f"`Transition:` {from_state} → {to_state}\n"
            f"`Trigger:` {trigger}\n"
            f"`New Heat Limit:` {heat_limit_new_pct:.1f}%"
        )
        await self._send(msg)

    async def _send(self, text: str) -> None:
        """Rate-limited async send. Max 1 per 10s."""
        import time
        elapsed = time.monotonic() - self._last_sent_at
        if elapsed < 10.0:
            await asyncio.sleep(10.0 - elapsed)
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )
        self._last_sent_at = time.monotonic()
```

## B. Daily PDF Generation (PyMuPDF — fitz.Story)

**Pattern: HTML string → `fitz.Story` → `fitz.DocumentWriter` → PDF bytes**
No system dependencies. Zero Docker complexity. Works on any Python 3.9+.

```python
# python_brain/ouroboros/pdf_generator.py

import fitz  # PyMuPDF
import markdown  # markdown library: markdown → HTML

def generate_post_mortem_pdf(
    date_str: str,
    mode_scorecard: dict,
    shadow_book: list[dict],      # vetoed trades that would have won
    slippage_analysis: dict,
    ouroboros_prescriptions: list[str],
) -> bytes:
    """
    POST-MORTEM PDF — 21:00 UTC (3 pages)
    Page 1: Global Scorecard + Shadow Book
    Page 2: Execution Slippage Analysis
    Page 3: Ouroboros Prescriptions for Tomorrow
    """
    # Build HTML sections
    page1_html = _build_scorecard_html(date_str, mode_scorecard, shadow_book)
    page2_html = _build_slippage_html(slippage_analysis)
    page3_html = _build_prescriptions_html(ouroboros_prescriptions)
    full_html = f"{page1_html}<p style='page-break-before:always'/>{page2_html}<p style='page-break-before:always'/>{page3_html}"

    # fitz.Story pipeline
    story = fitz.Story(html=full_html)
    writer = fitz.DocumentWriter("/tmp/aegis_postmortem.pdf")
    mediabox = fitz.paper_rect("a4")
    while True:
        device, more = writer.begin_page(mediabox)
        _, filled = story.place(mediabox + (-36, -36, 36, 36))  # margins
        story.draw(device)
        writer.end_page()
        if not more:
            break
    writer.close()
    with open("/tmp/aegis_postmortem.pdf", "rb") as f:
        return f.read()

def generate_morning_primer_pdf(
    date_str: str,
    macro_regime: dict,
    isa_discoveries: list[dict],
    hot_scanner_draft: list[dict],
    router_preferences: dict,
) -> bytes:
    """
    MORNING PRIMER PDF — 07:00 UTC London time (3 pages)
    Page 1: Macro Weather (HMM) + New ISA Discoveries
    Page 2: HotScanner Draft (promotions 🔼, demotions 🔽)
    Page 3: Smart Router Preferences (FX drag, ETP health)
    """
    page1_html = _build_macro_html(macro_regime, isa_discoveries)
    page2_html = _build_hotscanner_html(hot_scanner_draft)
    page3_html = _build_router_html(router_preferences)
    # ... same fitz.Story pattern as above
```

**PDF delivery:**
- PDFs saved to `/data/reports/YYYY-MM-DD/` on EC2
- Telegram send via `bot.send_document()` (the PDF file, not inline text)
- Both PDFs sent at their scheduled times regardless of trading activity

---

# TASK 5 — SYNTHESIZED MASTER PLAN: PHASES 11–15

## Pre-Phase Checklist: Proof That Phase 10 Is Complete

Before Phase 11 code begins, verify:
- [ ] All 200+ Phase 0–9 acceptance tests green (03_ACCEPTANCE_TESTS.md)
- [ ] Paper engine running live: 5+ consecutive trading days, zero HALT events
- [ ] Ouroboros pipeline completing nightly in < 120 minutes
- [ ] WAL replay verified: kill -9 → restart → identical position state
- [ ] 100-line invariant verified: proptest 10,000 transitions
- [ ] Zero orphaned positions after each session
- [ ] SIGTERM handler verified: kill -15 → positions flatten → clean exit

---

## PHASE 11 — DIRECT US EQUITIES + CORE GLOBAL INFRASTRUCTURE

**Duration estimate:** 173 hours (117h original + 56h audit fixes)
**Scope:** MODE B+, MODE C, DARK mode, Smart Router, Infinite Chandelier,
RiskGate 31 vetoes, Ouroboros upgrades, AUM scaling, telemetry stack.

### Phase 11 Pre-Conditions (P0 — Must Fix Before Any Code)

These 6 items are blocking. Phase 11 code cannot start until all are resolved.

| ID | Fix | Effort |
|----|-----|--------|
| SC-01 | SIGTERM handler + position flatten on shutdown | 8h |
| SC-02 | `clock.rs` ModeA boundary: `s >= 23*3600 \|\| s < 8*3600` | 1h |
| SC-03 | `avg_win`/`avg_loss` Ouroboros calibration (not hardcoded 0.02) | 6h |
| SC-04 | Replace yfinance in OFI hot path with IBKR subscription data | 4h |
| SC-05 | Exit signals must submit actual SELL order to IBKR broker | 6h |
| SC-06 | Minimum position size gate: £1,500 before Kelly order submission | 1h |

**SC-01 Detail:**
```rust
// In main.rs
use ctrlc;

fn register_shutdown_handler(engine: Arc<Mutex<TradingEngine>>) {
    ctrlc::set_handler(move || {
        let mut e = engine.lock().unwrap();
        e.initiate_graceful_shutdown();
        // initiate_graceful_shutdown():
        // 1. Set RiskRegime::Halt
        // 2. Submit MOC exits for all open positions
        // 3. Wait for fills (max 30s)
        // 4. Write SystemShutdown WAL event
        // 5. process::exit(0)
    }).expect("Failed to register SIGTERM handler");
}
```

**SC-05 Detail:** The current V1 codebase removes positions from the internal
state table on exit signal but never calls `ibkr.place_order(SELL_ORDER)`.
Fix: Every `ExitSignal` handler must call `broker.submit_exit_order()` and
write `EXIT_ORDER_SUBMITTED` to WAL before considering the position closed.

### Phase 11 Build Sequence

**Sprint 11.1 — Mode Infrastructure (2 weeks)**
- [ ] `mode_controller.rs` — 5-mode state machine with DST-aware boundaries
- [ ] `SubscriptionManager` — deterministic Mutex-based transition (100-line invariant)
- [ ] Proptest: 10,000 random mode transitions, line_count ≤ 100 always
- [ ] `from_utc_secs()` ModeA arm: `s >= 82800 || s < 28800`
- [ ] `mode_b_plus_end_utc()` using `ZoneInfo("Europe/London")` for BST/GMT
- [ ] APScheduler pre-LSE jobs: `timezone="Europe/London"` for all
- [ ] Phase 11 Gate: All mode boundary acceptance tests pass (AT-01 through AT-06 equivalent)

**Sprint 11.2 — Smart Router + Allocator (1.5 weeks)**
- [ ] `router.rs` — 6-step routing decision: ISA gate → ETP lookup → health check → cost comparison → ADR trap → size gate
- [ ] `allocator.rs` — Thompson Sampling multi-mode allocator with mode-aware min_fraction
- [ ] ETP health check: spread < 0.5% mid, ADV > configurable floor, IBKR health OK
- [ ] ETP-first principle: ETP wins over direct if health checks pass
- [ ] ISA gate: HMRC Table 1+2 hard blocklist (Taiwan, China, India blocked)
- [ ] Underlying tracking: subscribe underlying ONLY on fill, cancel on close
- [ ] Phase 11 Gate: Router routing tests, ETP-wins-over-direct validated, ISA blocklist enforced

**Sprint 11.3 — HotScanner + RotationScanner (1.5 weeks)**
- [ ] `hot_scanner.rs` — OFI/CUSUM/Kalman signal stack, per-mode signal dispatch
- [ ] `rotation_scanner.rs` — EXP3 bandit with geometric mean initialisation for new arms
- [ ] OFI fix: rename to QuoteImbalance, document divergence from Cont/Kukanov
- [ ] CUSUM: EWMA mean update α=0.02 (dynamic mean, not static)
- [ ] Kalman Q: intraday U-shaped adaptation (Q_eff = Q_base × vol_ratio_tod)
- [ ] Thompson Sampling: log-return reward with Normal-Normal conjugate prior
- [ ] Phase 11 Gate: Signal acceptance tests, signal path verified end-to-end

**Sprint 11.4 — Infinite Chandelier (1 week)**
- [ ] `exit_engine.rs` extended: infinite ladder with geometric decay M=3.0 × 0.85^(n-1)
- [ ] 8 adaptive multipliers: ToD, Regime, ProfitScale, MomentumDecay, ATRPercentile, MAECalibration, VolRegime, Session
- [ ] Ratchet invariant: `assert!(new_stop >= old_stop)` on every update
- [ ] Gap detection: ATR computed only on non-gapped bars (gap = |low - prev_close| / prev_close > 0.01)
- [ ] CARRIED/MONITORED state: Chandelier frozen (stop field read-only in these states)
- [ ] Phase 11 Gate: Chandelier ratchet proptest (stop never decreases), gap detection test

**Sprint 11.5 — RiskGate 31 Vetoes (1 week)**
- [ ] `risk_gate.rs` extended: 31 vetoes in 5 groups (G1 ISA, G2 Drawdown, G3 Market, G4 Execution, G5 Macro)
- [ ] 4-tier drawdown: NORMAL (0) / YELLOW (-3%) / ORANGE (-5%) / RED (-8%)
- [ ] CVaR heat: regime × VIX floating heat limit
- [ ] U-shaped spread curve: ToD bucket multipliers
- [ ] IBKR error code matrix: 162, 200, 201, 321, 354, 1100, 1102, 2104, 3200
- [ ] reqMarketDataType(3) call on startup and each reconnect
- [ ] Phase 11 Gate: All 31 vetoes unit tested, drawdown tier transitions validated

**Sprint 11.6 — Executioner V2 + ADV Execution (1 week)**
- [ ] `executioner_v2.rs` — TWAP/VWAP slicer with ADV cap (1% of 5-min rolling volume)
- [ ] Alpha half-life estimation: Redis read from Ouroboros IC decay analysis
- [ ] Order idempotency: persistent UUIDv7 order IDs, duplicate detection
- [ ] Reconciliation fix: `reqPositions()` on mode transitions (not local cache read)
- [ ] Settlement cycle tracker: T+2 tracking for free-riding prevention
- [ ] Phase 11 Gate: ADV slicing test (£8k order → 3 TWAP slices), idempotency test

**Sprint 11.7 — Ouroboros Phase 11 Upgrades (1.5 weeks)**
- [ ] Ouroboros step checkpointing: Redis `ouroboros_step_N_ts` after each of 9 steps
- [ ] Resume from last checkpoint on restart (not full 2h redo)
- [ ] 22:45 UTC watchdog timer: load last-valid calibration if `pipeline_complete` not set
- [ ] avg_win/avg_loss calibration: read last N closed trades from WAL, write to Redis
- [ ] yfinance removed from hot path: IBKR `reqHistoricalData` primary
- [ ] IBKR historical data rate limiter: 6-concurrent semaphore, 60 req/10min bucket
- [ ] Phase 11 Gate: Ouroboros checkpoint test, avg_win calibration verified

**Sprint 11.8 — Telemetry Stack (1 week)**
- [ ] Telegram reporter: 4 message types, async, rate-limited
- [ ] PyMuPDF post-mortem PDF (3 pages): scorecard, slippage, prescriptions
- [ ] PyMuPDF morning primer PDF (3 pages): macro, HotScanner draft, router prefs
- [ ] PDF scheduled: post-mortem at 21:05 UTC, primer at 07:00 UTC London
- [ ] Shadow Book: track all vetoed trades, compute counterfactual PnL
- [ ] Heartbeat: 30-second Ouroboros step progress in DARK, 4-hour heartbeat otherwise
- [ ] Phase 11 Gate: Telegram test messages sent, PDFs generated locally, no WeasyPrint

**Sprint 11.9 — AUM Scaling + Integration (1 week)**
- [ ] Logarithmic Kelly taper: `aum_scaling_fraction()` function
- [ ] ADV-bounded execution gate integrated with Kelly sizer
- [ ] Docker health check: `/healthz` HTTP endpoint, 30s polling, 503 if no tick > 60s
- [ ] SIGTERM handler with position flatten and graceful exit
- [ ] Ouroboros reconciliation: `reqPositions()` at each mode transition
- [ ] Memory monitoring: hard cap at 3.5GB (0.5GB headroom on c7i-flex.large)
- [ ] Log rotation: 7-day retention, gzip compression

### Phase 11 Gate Criteria (Required Before Phase 12)

- [ ] All 72 original acceptance tests green
- [ ] P0 fixes SC-01 through SC-06: all verified with actual terminal output
- [ ] SubscriptionManager proptest: 10,000 transitions, line_count ≤ 100 always
- [ ] Underlying tracking scope: zero underlying lines without open position
- [ ] MODE B+ boundary: dynamically computed, tracks BST/GMT automatically
- [ ] APScheduler timezone: `timezone="Europe/London"` verified in all pre-LSE jobs
- [ ] Minimum entry gate: Kelly < £1,500 skips trade (BELOW_MIN_SIZE in WAL)
- [ ] Dust guard: partial fill < £500 triggers immediate market exit
- [ ] SIGTERM handler: kill -15 → positions flatten within 30s → clean exit
- [ ] Chandelier ratchet: proptest confirms stop never decreases
- [ ] ADV execution: £8,000 position → TWAP sliced at 1% ADV
- [ ] avg_win calibration: Ouroboros reads WAL, not hardcoded 0.02
- [ ] Ouroboros checkpoint: EC2 restart during calibration resumes from last step
- [ ] Telegram messages: all 4 types sent, received, formatted correctly
- [ ] PDFs generated: post-mortem + primer, PyMuPDF only, no system deps
- [ ] 5 paper trading days: MODE B+ and MODE C, no HALT events, no ISA violations
- [ ] WAL audit: every mode transition, every fill, every exit in WAL

**Phase 11 total effort: 173 hours across 9 sprints**

---

## PHASE 12 — EUROPEAN DIRECT EQUITIES

**Duration estimate:** 75 hours
**Scope:** 15 European exchanges in MODE B, multi-currency (GBP/EUR/CHF/SEK/NOK/DKK/PLN),
FTT handling (France, Italy, Spain), XETRA closing auction cutoff, dual-listing dedup.

### Phase 12 Pre-Conditions

Phase 11 gate APPROVED. All tests green.

### Phase 12 Build Sequence

**Sprint 12.1 — European Universe Crawl (1 week)**
- [ ] `european_universe.py` — 15 exchange IBKR pull, hard filters, ISA gate
- [ ] ETP overlay: ASML → ASL3.L check, route to ETP if available
- [ ] Dual-listing deduplication: ISIN-based, highest ADV venue wins
- [ ] IBKR reqContractDetails rate limiter: 25 req/min (safe), pacing violation recovery
- [ ] Holiday calendar: all 15 European exchanges, checked at subscription time
- [ ] `exchange_profiles.toml`: all 15 exchanges, trading hours, tick sizes, board lots
- [ ] FX rate integration: EUR/CHF/SEK/NOK/DKK/PLN rates via IBKR, refreshed hourly

**Sprint 12.2 — Multi-Currency Kelly + FTT + Stamp Duty (1 week)**
- [ ] FX drag in Kelly: `apply_fx_drag()` for all non-GBP currencies
- [ ] FTT market-cap gate (Amendment A8): conditional FTT by market cap threshold
  - France: 0.3% only if market cap > €1B (check from Ouroboros reqContractDetails)
  - Italy: 0.1% only if market cap > €500M
  - Spain: 0.2% always (no threshold)
  - Ireland: 1.0% stamp duty always
- [ ] FTT intraday exemption (Amendment A2): FR and IT exempt if opened and closed same session
- [ ] `transaction_tax.rs`: `effective_rate_bps(market_cap_eur: f64)` method
- [ ] Router cost comparison: include FTT in cost model for ETP-vs-direct routing

**Sprint 12.3 — SubUniverseAllocator + XETRA + Line Budget (1 week)**
- [ ] `SubUniverseAllocator` — Thompson Sampling ETP vs European equity split
- [ ] Market-hours-aware min_fraction: `active_min_fraction()` scales by open exchanges
- [ ] XETRA closing auction cutoff: T-5 fires at 16:30 CET continuous close, not 17:35 closing auction
- [ ] Volatility override (Amendment A5): min_fraction suspended in BEAR_VOLATILE + VIX>25 + flat
- [ ] Tick size dynamic reload (Amendment A6): monthly IBKR refresh for MiFID reclassifications
- [ ] Phase 12 Gate: SubUniverseAllocator proptest, line budget verified, FTT test cases

**Sprint 12.4 — Integration + Telemetry (0.5 week)**
- [ ] Morning primer: European ETP health report in PDF2 (FX drag, stamp duty impact)
- [ ] Post-mortem: EUR/CHF position PnL in GBP equivalent
- [ ] MODE B+ triple overlap: LSE ETPs + European + US simultaneously
- [ ] 5 paper trading days: full European universe, no ISA violations, FTT correctly applied

### Phase 12 Gate Criteria

- [ ] All 40 Phase 12 acceptance tests green
- [ ] FTT market cap gate: Renault (low cap) → 0% FTT, LVMH → 30bps
- [ ] Active min fraction: closed exchanges get zero allocation
- [ ] XETRA T-5: verified correct UTC time with DST
- [ ] Dual-listing dedup: LVMH not subscribed on both EPA and XETRA
- [ ] FTT intraday exemption: French position opened+closed same session → 0 FTT
- [ ] Commission model: tiered vs fixed loaded from IBKR account settings
- [ ] 5 paper trading days: MODE B + B+ European universe, no violations

**Phase 12 total effort: 75 hours across 4 sprints**

---

## PHASE 13 — ASIA-PACIFIC SESSION + DARK MODE

**Duration estimate:** 95 hours
**Scope:** MODE A (23:00–08:00 UTC), DARK mode (21:00–23:00 UTC), TSE/HKEX/ASX/SGX/KRX,
overnight carry state machine, IBKR 04:45 UTC reset handling, 6 Asian exchange profiles.

### Phase 13 Pre-Conditions

Phase 12 gate APPROVED. All tests green.

### Phase 13 Build Sequence

**Sprint 13.1 — Asian Exchange Infrastructure (1.5 weeks)**
- [ ] `asian_exchange.rs` — 6 exchange profiles (TSE, HKEX, ASX, SGX, KRX, NZX-disabled)
- [ ] Board lot rounding: TSE (100/1000), KRX (1), HKEX (varies), ASX (1), SGX (100), KRX (1)
- [ ] Daily price limits: TSE (±30% static, dynamic circuit breaker), KRX (±30%)
- [ ] Lunch break detection: TSE (02:30–03:30 UTC), HKEX (04:00–05:00 UTC)
- [ ] `clock.rs` extensions: `is_mode_a()`, `is_dark_mode()`, `is_tse_lunch()`, `is_hkex_lunch()`
- [ ] ASX DST handling: AEDT (00:00 UTC open) vs AEST (23:00 UTC open)
- [ ] NZX: DISABLED (NZST/DARK conflict unresolved until carry state machine extension)
- [ ] KRX Volatility Interruption (VI): 10% in 1min → VetoReason::VolatilityInterruptionActive
- [ ] Phase 13 Gate: All 50 acceptance tests green, clock boundary tests AT-01 through AT-06

**Sprint 13.2 — DARK Mode + Ouroboros DARK Pipeline (1.5 weeks)**
- [ ] DARK mode enforcement: DarkModeActive check BEFORE veto chain, double-gated at Executioner
- [ ] Ouroboros DARK pipeline: fires at 21:00 UTC on ModeC→Dark WAL event (not cron)
- [ ] 9-step pipeline: step checkpointing via Redis, resume from last step on restart
- [ ] 22:45 UTC watchdog: loads last-valid calibration if `pipeline_complete` not set
- [ ] Asia universe discovery (STEP 1): IBKR pull, ISA gate (Taiwan/China/India hard blocked)
- [ ] ETP/GDR overlay: TSM3.L > TWSE TSMC, SMSN.IL > KRX Samsung, BAB3.L > HKEX Alibaba
- [ ] IBKR server reset 04:45 UTC: reconnect with exponential backoff (5s/15s/45s)
- [ ] Post-reconnect re-subscription: priority order (carry → active → scan queue)
- [ ] Phase 13 Gate: DARK mode zero-trade test, Ouroboros fires at 21:00 UTC

**Sprint 13.3 — Overnight Carry State Machine (1 week)**
- [ ] `overnight_carry.rs` — CarryState: LIVE → CARRIED → MONITORED → REACTIVATED → CLOSED
- [ ] New state: HALTED (exchange circuit breaker frozen, Amendment A10)
- [ ] Max 6 carry positions: `try_carry()` returns Err at cap, Telegram alert sent
- [ ] reqPnL subscription (not snapshot polling): Amendment A2
- [ ] Stop freeze in CARRIED/MONITORED: `frozen_stop` field is immutable in these states
- [ ] Deferred market exit: `DeferredMarketExit` queued when stop breached during DARK
- [ ] Gap-down alert: >10% intraday drop from last known price → Telegram SYSTEM SHIFT
- [ ] Holiday calendar check: IBKR `reqTradingHours` at each MODE C → DARK transition
- [ ] HALTED → MONITORED recovery: exchange resumes OR 2 trading days elapsed
- [ ] Phase 13 Gate: Carry state machine proptest, carry cap test (7th position rejected)

**Sprint 13.4 — Asian Telemetry + Integration (0.5 week)**
- [ ] Cross-timezone intelligence: DCC-GARCH derived HKEX/Nikkei/ASX sentiment weights
- [ ] Morning primer: Asia section (overnight moves, TSE/HKEX pre-open setup)
- [ ] Post-mortem: MODE A P&L breakdown by exchange, FX drag by currency
- [ ] KRX/TSE VetoReason alerts: circuit breaker events in WAL + Telegram SYSTEM SHIFT
- [ ] 5 paper trading days: full MODE A coverage, no pacing violations, no 100-line breach

### Phase 13 Gate Criteria

- [ ] All 50 acceptance tests green (AT-01 through AT-50)
- [ ] IBKR disconnect at 04:45 UTC: reconnect within 3 minutes, carry positions reconciled
- [ ] NZX: DISABLED (logged, not an error — waiting for NZST/DARK conflict fix)
- [ ] ASX DST: AEST period uses 23:00 UTC open, AEDT uses 00:00 UTC
- [ ] reqPnL subscription: carry positions monitored without pacing violations
- [ ] KRX VI detection: 10%/1min triggers VetoReason::VolatilityInterruptionActive
- [ ] HKD concentration: counted as 80% USD equivalent in limit calculation
- [ ] DCC-GARCH cross-TZ weights: no hardcoded 0.45/0.35/0.20
- [ ] Carry cap: 7th carry rejected, CARRY_CAP_REACHED in WAL, Telegram alert
- [ ] HALTED state: KRX limit hit → HALTED → no orders → resume MONITORED after 2 days
- [ ] 5 paper trading days: full MODE A, zero ISA violations, 100-line invariant holds

**Phase 13 total effort: 95 hours across 4 sprints**

---

## PHASE 14 — INSTITUTIONAL HARDENING

**Duration estimate:** 80 hours
**Scope:** Production infrastructure: memory management, EC2 resilience, WAL compaction,
meta-labeler upgrade, half-Kelly enforcement, VIX real-time sourcing for MODE A.

### Phase 14 Build Sequence

**Sprint 14.1 — Production Resilience (2 weeks)**
- [ ] WAL compaction: weekly archive to S3, retain last 30 days in SQLite
- [ ] EC2 IMDS spot termination notice: poll `169.254.169.254` every 30s, 2-min graceful shutdown
- [ ] Crossbeam channel: bounded with backpressure (drop oldest on overflow, never unbounded)
- [ ] Redis memory: `maxmemory 512mb`, `maxmemory-policy noeviction`, keyspace TTLs
- [ ] Docker health check: `/healthz` 30s polling, 503 on no-tick > 60s
- [ ] Rust panic handler: `panic = "abort"` + custom hook writes `SystemPanic` to WAL
- [ ] Log rotation: logrotate 7-day retention, gzip compression
- [ ] Memory monitoring: hard cap at 3.5GB, OOM kill prevention

**Sprint 14.2 — Meta-Labeler Upgrade (1 week)**
- [ ] Minimum sample size guard: skip meta-labeler classification if < 1,000 samples
- [ ] Purged k-fold cross-validation: implement de Prado Chapter 7 purging
- [ ] F1-optimal threshold: PR curve via Ouroboros, not default 0.5
- [ ] Half-Kelly enforcement: until 250 validated live trades, all Kelly fractions × 0.5
- [ ] OFI → QuoteImbalance rename: update all code references, add documentation note
- [ ] VPIN: exchange-scoped bucket reset at each exchange's market open

**Sprint 14.3 — VIX + Macro Enhancements (1 week)**
- [ ] VIX for MODE A: real-time proxy (VX futures or CBOE VIX API) — not stale prior-day close
- [ ] VIX staleness guard: if VIX reading > 6 hours old during MODE A → log WARNING, use
  conservative default (VIX = 25) for CVaR heat calculation
- [ ] DCC-GARCH intraday: lightweight 30-min DCC update during active sessions (arch library)
- [ ] CUSUM: symmetric with EWMA mean — static μ violation fully eliminated
- [ ] Kyle's Lambda: tick-level OLS on signed trades, not 1-minute bars

**Sprint 14.4 — ISA Compliance Hardening (0.5 week)**
- [ ] ETN vs ETP classification: reject debt-structured ETNs from ISA universe
- [ ] ISA eligibility table max staleness: 48h — if older than 48h, HALT new entries
- [ ] Free-riding detection: flag unsettled BUY proceeds used for new BUY
- [ ] GDR/ADR deduplication: TSE equity + NYSE ADR cannot both be in universe simultaneously

### Phase 14 Gate Criteria

- [ ] EC2 spot interruption test: IMDS termination notice → graceful shutdown in < 2 min
- [ ] WAL compaction: week-old records compressed to S3, last 30 days in SQLite
- [ ] Redis OOM test: fill Redis to 90% → noeviction policy holds, no key loss
- [ ] Meta-labeler guard: < 1,000 samples → returns neutral (0.5 confidence)
- [ ] Half-Kelly: trade count < 250 → all Kelly outputs halved
- [ ] VIX staleness: MODE A with stale VIX → CVaR uses conservative default
- [ ] ISA ETN gate: reject ETN-structured instruments from universe

**Phase 14 total effort: 80 hours across 4 sprints**

---

## PHASE 15 — THE CRUCIBLE: LIVE VERIFICATION

**Duration estimate:** 40 hours
**Scope:** End-to-end dry runs, flash-crash simulation, timezone transition validation,
partial fill state machine validation, telemetry verification. No live capital until
ALL 7 test suites pass.

**This phase is the mathematical proof that the system operates flawlessly.**

### Test Suite 1: The Global 24/5 Dry Run

**5 consecutive 24-hour dry runs** (Monday–Friday, full week):
- MODE A (23:00–08:00): scan TSE, HKEX, ASX, SGX, KRX
- MODE B (08:00–14:30): LSE ETPs + European equities
- MODE B+ (14:30–LSE_CLOSE): hybrid overlap
- MODE C (16:30–21:00): US/Canada direct equities
- DARK (21:00–23:00): Ouroboros pipeline, zero trading

**Pass criteria:**
- Zero HALT events across all 5 days
- Zero 100-line invariant violations
- Zero ISA compliance breaches
- Ouroboros completes within 2 hours each night
- All PDFs generated and delivered via Telegram
- WAL: complete audit trail, every event logged
- Reconciliation: zero discrepancies between system state and IBKR

### Test Suite 2: Simulated Flash Crash

**Synthetic data injection:** QQQ3.L drops -35% in 8 minutes at 14:35 UTC.

**Required system behaviour:**
1. CUSUM triggers within first -2% move
2. Chandelier stop fires at calibrated ATR level
3. Executioner submits limit order at bid
4. If limit not filled within 60s → escalate to MTL
5. RiskGate RED tier activates at -8% daily loss
6. RED tier: all new entries blocked, existing positions closing only
7. Telegram: CHANDELIER SEVERED (with exit mechanic), then SYSTEM SHIFT (RED tier)
8. No orphaned positions after the crash
9. Recovery: ORANGE → YELLOW → NORMAL as losses recover

**Pass criteria:**
- Position exits within 5 minutes of Chandelier trigger
- Maximum slippage from stop to fill < 2% (not -35%)
- System not in HALT after flash crash
- Recovery to NORMAL within 4 hours

### Test Suite 3: 100-Line Routing Handoff (Mode Transitions)

**Sequence tested:**
1. MODE C → DARK at 21:00 UTC: all MODE C lines cancelled (except 3 carries)
2. DARK → MODE A at 23:00 UTC: 94 Asian lines subscribed (3 carry reserved)
3. MODE A → MODE B at 08:00 UTC: Asian lines cancelled, European subscribed
4. MODE B → MODE B+ at 14:30 UTC: 20 US lines added (LSE adjusted)
5. MODE B+ → MODE C at 16:30 UTC: LSE released, 100 US lines

**Proptest during each transition:**
```rust
// 10,000 random combinations of:
//   carry_count ∈ [0, 6]
//   active_positions ∈ [0, 3]
//   scan_demand ∈ [0, 94]
// Assert: total_lines = carry × 2 + active × 2 + scan ≤ 100
```

**Pass criteria:**
- Zero line_count violations across 10,000 random combinations
- Transition time < 2,000ms for each mode boundary
- No data feed gaps during transitions (ticks continue for carry positions)

### Test Suite 4: Partial Fill State Machine

**Scenarios tested:**
1. Full fill: 200 shares intended, 200 filled → FILLED state, stop registered
2. Partial fill: 200 intended, 37 filled, alpha decays → cancel remaining, dust guard fires
3. Partial fill: 200 intended, 37 filled, alpha holds → 37 shares position held, stop registered
4. Phantom fill: cancel sent, fill arrives 50ms later → fill takes precedence
5. Orphan: order submitted, no ACK in 5s → orphan recovery, reqOpenOrders diff
6. Duplicate submission: same (ticker, side) within 60s → second rejected silently

**Pass criteria:**
- All 6 scenarios produce correct terminal state
- No orphaned positions after each scenario
- WAL contains complete event trail for each scenario
- Dust guard fires correctly at £500 floor

### Test Suite 5: SIGTERM / Container Restart Validation

**Sequence:**
1. Open 3 positions in MODE B
2. Send `kill -15` (SIGTERM) to engine process
3. Verify: SIGTERM handler fires, MOC exit orders submitted
4. Verify: fills received within 30s
5. Verify: `SystemShutdown` WAL event written
6. Verify: IBKR positions = 0 after shutdown
7. Restart engine: verify WAL replay, reconciliation, positions = 0

**Pass criteria:**
- IBKR shows zero open positions after SIGTERM+30s
- No orphaned positions at IBKR after restart
- WAL audit trail complete

### Test Suite 6: Telemetry Integration Test

1. **Telegram delivery:** Trigger each of 4 message types, verify receipt < 30s
2. **PDF delivery:** Post-mortem at 21:05 UTC, primer at 07:00 UTC London, verify receipt
3. **Shadow Book:** Execute 10 vetoed signals, verify counterfactual PnL tracked
4. **Heartbeat:** 4-hour heartbeat verified, DARK step progress messages verified
5. **Rate limit:** Trigger 10 rapid Telegram sends, verify 10s rate limiting holds

**Pass criteria:**
- All Telegram messages received within 30s of trigger
- PDFs: text readable, charts render, no missing sections
- Shadow Book: 10 entries with correct counterfactual

### Test Suite 7: 100-Trade Validation Gate (Romano-Wolf)

**Before ANY live capital:** 100 paper trades across all 5 modes with:
- Win rate ≥ 40% (Romano-Wolf family-wise error rate controlled test)
- Sharpe ratio > 0 (after all costs)
- Maximum intraday drawdown < 8% in any single session
- Zero ISA violations
- Zero HALT events from system errors (HALT from drawdown acceptable)
- Ouroboros: all 9 steps complete each night
- Telemetry: PDFs generated, Telegram functional, every day

**This gate requires 100 actual paper trades.** The 100 trades must span:
- At least 15 MODE A trades
- At least 40 MODE B/B+ trades
- At least 30 MODE C trades
- At least 5 carry events (mega-runners)

### Phase 15 Gate Criteria (Live Capital Authorization)

**ALL of the following must be signed off in writing before £1 of live capital:**

- [ ] Test Suite 1: 5-day dry run — ZERO violations
- [ ] Test Suite 2: Flash crash simulation — position exits within 5 min, recovery confirmed
- [ ] Test Suite 3: Proptest 10,000 transitions — line_count ≤ 100 always
- [ ] Test Suite 4: 6 partial fill scenarios — all produce correct state
- [ ] Test Suite 5: SIGTERM test — IBKR shows zero positions after 30s
- [ ] Test Suite 6: Telemetry — all 4 Telegram types, both PDFs, shadow book working
- [ ] Test Suite 7: 100 paper trades — WR ≥ 40%, Sharpe > 0, zero HALT from errors
- [ ] Human review: WAL audit from all 7 test suites reviewed and approved
- [ ] Position sizing: live Kelly fractions reviewed against AUM scaling function

**Phase 15 total effort: 40 hours across 7 test suites**

---

# TASK 6 — THE CRUCIBLE INTEGRATION (Formal Definition)

## What the Crucible Means

The Crucible is not "paper trading until we feel good." It is a formal,
mathematically rigorous proof that the system operates correctly. Every test
suite has objective pass/fail criteria. There are no subjective judgements.
The system either passes or it doesn't.

**The Crucible begins only after Phase 15 conditions are met.**

## The 63-Day Gauntlet (Post-Crucible)

After the Crucible (Test Suite 7, 100 trades), before scaling to full AUM:

```
Days 1–30:    £1,000 starting (1/10 of actual ISA) — proving the system
Days 31–60:   Scale to £5,000 if WR ≥ 40% and no HALT from errors
Days 61–63:   Review gate — meets target? Scale to full ISA? Or refine?
```

The gauntlet is the final proof. Capital is at risk but the position is tiny.
The system proves itself on real fills, real slippage, real market microstructure.

## The Blood Oath (Non-Negotiable Pre-Conditions for Live)

These are not guidelines. They are structural guarantees:

1. **SIGTERM handler verified:** Container restart cannot orphan positions.
2. **100-line invariant holds under proptest:** Server-side rejection impossible.
3. **Exit signal submits actual IBKR orders:** Positions cannot be "closed" locally
   while remaining open at the broker.
4. **avg_win/avg_loss calibrated from WAL:** Kelly fractions are not 4× too large.

**If any of these four items fails a verification test at any point after live
capital is deployed, all positions are immediately flattened and the system
is taken offline for root cause analysis.**

---

# SUMMARY — TIMELINE AND EFFORT

| Phase | Scope | Effort | Gate |
|-------|-------|--------|------|
| Pre-Phase 11 | P0 fixes (SC-01–SC-06) | 26h | ✅ Required before Phase 11 code |
| Phase 11 | US equities + Core infrastructure | 173h | 72 tests + 5 paper days |
| Phase 12 | European equities | 75h | 40 tests + 5 paper days |
| Phase 13 | Asia-Pacific + DARK mode | 95h | 50 tests + 5 paper days |
| Phase 14 | Institutional hardening | 80h | 7 gate criteria |
| Phase 15 | Crucible: 7 test suites | 40h | 100-trade gate + human sign-off |
| **TOTAL** | | **489h** | **Live capital authorized** |

**At 20h/week:** ~24 weeks (6 months) to live capital.
**At 40h/week:** ~12 weeks (3 months) to live capital.

---

# BINDING CONSTRAINTS (Cannot Be Violated, Ever)

1. **100-line invariant:** `active_lines() ≤ 100` always. Proptest on every CI build.
2. **ISA safety:** No short selling. No margin. Checked on every order.
3. **SIGTERM + position flush:** Container restart cannot orphan broker positions.
4. **WAL is God:** Redis is cache. ndjson WAL is truth. WAL wins all disputes.
5. **Exit submits IBKR order:** Exit signal is not complete until broker confirms fill.
6. **Fail-closed:** Unknown state → HALT. Never guess.
7. **Minimum position £1,500:** Kelly output below £1,500 = skip trade.
8. **AUM taper:** Logarithmic Kelly scaling as ISA grows. Full Kelly only at £10k.
9. **Ouroboros owns DARK:** Zero trading 21:00–23:00 UTC. Double-gated.
10. **DARK means DARK:** No new subscriptions, no new orders, no signal evaluation.
    The only IBKR activity is reqPnL monitoring for carry positions (passive, read-only).

---

*AEGIS_MASTER_PLAN_v17.md*
*Version: 17.0 — The Definitive Plan*
*Supersedes: All previous master plan versions*
*Status: AWAITING APPROVAL — respond with "APPROVED" to begin Phase 11 Pre-Conditions*
*Next action: SC-01 through SC-06 (P0 fixes, 26h total) — blocking all Phase 11 code*
