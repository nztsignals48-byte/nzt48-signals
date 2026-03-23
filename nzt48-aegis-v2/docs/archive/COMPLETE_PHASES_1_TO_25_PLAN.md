# COMPLETE IMPLEMENTATION PLAN — PHASES 1 TO 25
## From Today's Wiring to Live Capital (Full Timeline)

**Status**: READY FOR SEQUENTIAL EXECUTION
**Phases**: 0 (blockers) + 1-25 (implementation)
**Total Hours**: 7.5 (blockers) + 443.5 (phases) = **451 hours**
**Timeline**: At 30h/week: **3.5 months** (Late June 2026)

---

## PHASE 0 — CRITICAL BLOCKERS (7.5 hours) — TODAY

**Must complete before Phase 1 starts.**

- fs::write() sync_all (30 min)
- Reconciliation audit log (2h)
- Hayashi-Yoshida correlation (4h)
- cli.py atexit (already done)

**Gate**: `cargo test --lib` → 556+ tests pass

---

## PHASE 1 — TRUTH LAYER (COMPLETE)

**Status**: ✅ ALREADY DONE (Phase 1 from master plan)

What was completed:
- ✅ Real L1 bid/ask subscriptions (12 streams active)
- ✅ ISA annual counter wired
- ✅ GARCH leverage scaling applied
- ✅ BST hardcoded (correct dates)
- ✅ Backoff with jitter
- ✅ Crontab at 18:00 ET

**Gate**: 410 tests passing, zero warnings

---

## PHASE 2 — PERSISTENCE HARDENING (TBD, ~20h)

Make every state change survive restarts, crashes, power loss.

**Deliverables**:
- Reconciliation divergence audit log (WAL event)
- Parameter history archiving (TOML snapshot)
- Daily state snapshots (recovery point)

**New tests**: 3 acceptance tests

**Gate**: Simulate power loss at random times; state recovers to last known good

---

## PHASE 3 — DEAD CODE RESURRECTION (TBD, ~25h)

Wire the modules that exist but are never called.

**Deliverables**:
- HotScanner fully wired (Mode A volatility detection) ← **TODAY**
- RotationScanner fully wired (Mode B sector rotation) ← **TODAY**
- GarchInference integrated (real-time vol forecasting)
- StudentTKalman robust filtering (spoofed quote rejection)

**New tests**: 8 acceptance tests

**Gate**: All scanners fire signals; GARCH updates; Kalman filters clean prices

---

## PHASE 4 — TELEMETRY & OBSERVABILITY (TBD, ~22h)

Lock-free atomic counters for real-time system health.

**Deliverables**:
- Telemetry counters (trades/min, signals/min, errors/min)
- Health dashboard snapshot (JSON every 10s)
- Circuit breaker + panic guard + watchdog

**New tests**: 6 acceptance tests

**Gate**: Telemetry correctly counts; dashboard shows live stats

---

## PHASE 5 — QUANTITATIVE MATH PATCHES (TBD, ~18h)

Implement 4 cutting-edge academic models.

**QM-1: EVT (Extreme Value Theory)**
- GARCH residuals → GPD tail fit → VaR accuracy +40-60%
- File: garch_evt.rs (~500 lines)

**QM-2: Hayashi-Yoshida Covariance** ← **TODAY (Phase 0.3)**
- Async tick bucketing → correct correlation across exchanges
- File: hayashi_yoshida.rs (~400 lines)
- Benefit: Hedging false signals -30-50%

**QM-3: Log Thompson Sampling**
- Gaussian Bandit → lognormal posterior → momentum learning fixed
- File: log_thompson_sampler.rs (~400 lines)
- Benefit: Regret reduction +15-25%

**QM-4: Student-t Kalman Filter** ← **Partially in Phase 0.3**
- Mahalanobis weighting → outlier rejection → robustness +60%
- File: student_t_kalman.rs (~300 lines)

**New tests**: 4 acceptance tests (1 per QM model)

**Gate**: QM1 VaR accuracy verified on backtest; QM2-4 unit tests pass

---

## PHASE 6 — MULTI-SESSION ARCHITECTURE (TBD, ~35h)

5-mode trading clock + subscription rotation.

**Deliverables**:
- 5-mode clock: Dark → Mode A → Auction → Mode B → Mode B+ → Auction → Carry → Dark
- SessionManager: State machine, transitions, entry freezes
- SubscriptionManager: IBKR 100-line rotation every 5s
- Mode A (00:00-07:50 UTC): Asian session (TSE, HKEX, ASX, NZX)
- Mode B (08:00-14:30 UTC): European session (LSE, XETRA, Euronext)
- Mode B+ (14:30-16:30 UTC): US overlap (NYSE, NASDAQ added)
- Carry (16:35-23:45 UTC): Hold positions, freeze stops
- Dark (21:00-23:00 UTC): Ouroboros nightly calibration

**New tests**: 12 acceptance tests (mode boundaries, rotations, freezes)

**Gate**: Mode transitions logged; subscriptions rotate correctly; no trades during Dark/Auction

---

## PHASE 7 — SUBSCRIPTION MANAGER & CONSERVATION RULE (TBD, ~15h)

Dynamic ticker rotation to access 20,000+ universe.

**Deliverables**:
- SubscriptionManager line budget tracking (100-line IBKR limit)
- Rotation algorithm: Rank by conviction, swap every 5s
- Mode-boundary subscription swaps (Asia ↔ Europe)
- Scanner conservation: HotScanner/RotationScanner don't trigger new subscriptions
- Line reconciliation: 5-min checks vs IBKR, auto-heal divergences

**New tests**: 8 acceptance tests

**Gate**: Rotation never exceeds 100 lines; reconciliation catches divergences

---

## PHASE 8 — PRE-CONDITIONS & WIRING PATCHES (TBD, ~77h)

**This is the big one: Wiring everything together.**

**Deliverables**:
- RotationScanner fully wired into Mode B ← **TODAY (Phase 2)**
- HotScanner fully wired into Mode A ← **TODAY (Phase 3)**
- Mode B+ wiring (US lines added) ← **TODAY (Phase 4)**
- Reconciliation audit log in engine ← **TODAY (Phase 0.2)**
- fs::write() sync_all everywhere ← **TODAY (Phase 0.1)**
- Hayashi-Yoshida correlation (replace Pearson) ← **TODAY (Phase 0.3)**
- Python bridge: apex_snapshot message handling
- Risk arbiter: 31 checks integrated with multi-session logic
- All dead code paths connected and tested

**New tests**: 26 acceptance tests (20 standard + 6 wiring patches)

**Gate**: 560+ tests passing; all scanners fire; all modes transition cleanly

---

## PHASE 9 — CROSS-ASSET MACRO (TBD, ~20h)

VIX + DXY + Credit spreads + Fear & Greed regime detection.

**Deliverables**:
- VIX tracking (volatility regime: quiet/turbulent)
- DXY tracking (FX regime: USD strength)
- Credit spreads (leverage regime: tight/wide)
- Fear & Greed index (sentiment)
- Regime blending: 4 quadrants (bull-quiet, bull-volatile, bear-quiet, bear-volatile)
- Regime-aware Kelly scaling (position sizing adjusts by regime)

**New tests**: 5 acceptance tests

**Gate**: Regimes correctly classified; Kelly fraction adjusts with regime

---

## PHASE 10-15 — MODULES WIRING (Est. 120h total)

### Phase 10: Quote Imbalance & VPIN
- Buy/sell imbalance detection
- Volume-Synchronized Probability of Informed Trading

### Phase 11: Ouroboros Hardening
- Nightly pipeline: 10 steps fully wired
- Bayesian win rate calculation
- Kelly accelerator (adaptive sizing)
- Exit calibration (Chandelier multiplier optimization)
- Alpha sieve (IC tracking)
- GARCH calibration nightly
- TOML persistence (sync_all, no corruption)

### Phase 12: Smart Router & ISA Gate
- ISA eligibility per ticker (HMRC rules)
- Smart routing: ETP vs direct equity (cost comparison)
- Slippage minimization (TWAP)
- FX hedging triggers (GBP/USD)

### Phase 13: HotScanner & RotationScanner Refinement
- QM-3 & QM-4 integration (Thompson + Kalman)
- Score thresholds tuned
- Signal merging (when both scanners fire)

### Phase 14: Infinite Chandelier & Executioner V2
- 5-rung profit ladder (Le Beau 1999)
- Adaptive multiplier (8 multipliers)
- Order lifecycle management
- Trade attribution (which strategy?)

### Phase 15: RiskGate 31 Vetoes
- QM-1 EVT integration
- All 31 checks wired and tested
- Veto reasons logged

---

## PHASE 16 — OUROBOROS COMPLETION (Est. 52h)

**Complete nightly learning pipeline.**

**Deliverables**:
- WAL ingestion (all 10 event types)
- Bayesian win rate per ticker per strategy
- DSR (Differentially Sharpe Ratio) calculation
- Kelly accelerator with EWA blending
- Exit calibration: Chandelier tuning via realized P&L
- Regime hunting: HMM on market returns
- Alpha sieve: Information coefficient tracking
- GARCH(1,1) calibration daily
- QM-1 EVT: GPD tail fitting on residuals
- TOML writer: dynamic_weights.toml + universe_classification.toml

**New tests**: 10 acceptance tests

**Gate**: 100 paper trades analyzed; weights update correctly nightly

---

## PHASE 17 — TELEMETRY COMPLETION (Est. 18h)

**Live system health dashboard.**

**Deliverables**:
- Real-time counters (trades/min, signals/min, errors/min)
- P&L tracking (daily, weekly, monthly)
- Drawdown monitoring (current, max, rolling)
- Regime state display (current, transition history)
- System health (CPU, memory, IBKR connection, Redis)
- Terminal dashboard (curses-based, auto-refresh)

**New tests**: 5 acceptance tests

**Gate**: Dashboard displays correctly; metrics update every 10s

---

## PHASE 18-21 — GLOBAL MULTI-EXCHANGE (Est. 80h total)

### Phase 18: European Session Hardening (LSE, XETRA, Euronext)
- Exchange-specific features (board lots, trading halts, circuit breakers)
- Currency adjustments (GBP/EUR cross rates)

### Phase 19: Asian Session (TSE, HKEX, ASX, NZX)
- Exchange profiles (open times, lunch breaks, DST)
- Board lot registries
- Dynamic circuit breaker handling

### Phase 20: US Session (Phase 1)
- NYSE/NASDAQ integration
- Extended hours (pre-market, after-hours)
- Options support (Phase 21)

### Phase 21: Cross-Timezone Intelligence
- QM-2 Hayashi-Yoshida (async correlation)
- Overlapping session analysis
- Gap risk detection

---

## PHASE 22 — INSTITUTIONAL HARDENING (Est. 47h)

**Production-grade safety and reliability.**

**Deliverables**:
- Circuit breaker pattern (auto-halt on error bursts)
- Panic guard (graceful degradation)
- Watchdog (detect hangs, restart)
- Dead letter queue (failed orders recovery)
- Crash dump & core analysis
- Canary deployments (test on small % of capital)
- Health check endpoint (external monitoring)
- Backup strategy (S3 daily snapshots)

**New tests**: 15 acceptance tests

**Gate**: Inject 100 random failures; system recovers cleanly each time

---

## PHASE 23 — CRUCIBLE: 100-TRADE VALIDATION (Est. 40h)

**Final test before live capital.**

**7 test suites**:
1. **Suite A**: VanguardSniper strategy (40 trades)
2. **Suite B**: HotScanner strategy (30 trades)
3. **Suite C**: RotationScanner strategy (20 trades)
4. **Suite D**: Mixed mode (Mode A + B + B+) (20 trades)
5. **Suite E**: Extreme conditions (VIX spike, gap) (20 trades)
6. **Suite F**: Recovery scenarios (crash, restart) (15 trades)
7. **Suite G**: Edge cases (micro-liquidity, halts) (10 trades)

**Success criteria**:
- Win rate ≥ 40%
- Sharpe ratio ≥ 0.5
- Max drawdown < 8%
- Zero silent failures

**Gate**: Manual review by human trader (required before live)

---

## PHASE 24-25 — QUANTUM APEX (Future, not in scope)

**If Phases 1-23 validate:** Deep learning + neural networks.

- Rust FFI to Python (PyO3)
- DQN (Deep Q-Network) for strategy selection
- Neural Hawkes for event prediction
- DPDK kernel bypass (ultra-low latency)

---

# HOW THE COMPLETE SYSTEM WORKS (LAYMAN'S TERMS)

## THE ROBOT'S BRAIN (5 Components)

### 1. **The Watchdog (Always Watching)**
Monitors the main engine every second. If it freezes:
- Saves state to disk
- Kills and restarts the engine
- Resumes from last known good position

**Why it matters**: Prevents hanging with open positions at risk

---

### 2. **The Signal Generators (2 Strategies)**

#### **Strategy A: HotScanner (Volatility)**
- Runs during Asia hours (Mode A: 00:00-07:50 UTC)
- Watches 20,000+ candidates (rotates via SubscriptionManager)
- Looks for: Sudden price spikes on high volume
- Scores: 0-100 based on momentum + volume + volatility
- Fires when: Score > 70 (high conviction)
- Example: "Tokyo stock just spiked 1.5% on 3x normal volume. This is the START of a trend."

#### **Strategy B: RotationScanner (Sector Rotation)**
- Runs during Europe hours (Mode B: 08:00-14:30 UTC)
- Watches 20,000+ candidates via rotation
- Looks for: Which industries are winning today
- Scores: 0-100 based on relative strength vs market average
- Fires when: Score > 70 AND sector improved
- Example: "Banks beat the market average. Strongest bank is TSL3.L. High conviction."

---

### 3. **The Risk Controller (RiskGate - 31 Checks)**

Before ANY trade, the robot asks:

```
✓ Will this position lose >2% in a flash crash? (EVT VaR check)
✓ Is portfolio leverage already too high? (CHECK 1-3)
✓ Will I run out of cash if 3 positions go bad? (CHECK 4-6)
✓ Are spreads too wide to trade? (CHECK 7-9)
✓ Is volatility in extreme state? (Cross-asset macro check)
✓ Is liquidity drying up? (CHECK 10-15)
✓ Will this violate ISA £20k annual limit? (CHECK 16-18)
✓ Is execution going to slip too much? (CHECK 19-21)
✓ Are there extreme market conditions? (CHECK 22-31)
```

If ANY check fails → BLOCK the trade.

---

### 4. **The Allocator (Thompson Sampling Bandit)**

Learns which signals make the most money.

**Every night**, Ouroboros:
- Analyzes today's trades
- Calculates win rate per signal type
- Allocates capital proportional to performance
- Uses QM-3 (log-transform) to handle momentum skew

**Result**: Capital automatically flows to winning signals.

---

### 5. **The Execution Engine (Smart Router)**

Takes an approved signal and executes intelligently.

**Does NOT** place one big order (would move market, lose money).

**Instead**:
- Breaks order into 10 small chunks
- Spreads over 15 minutes (TWAP)
- Monitors bid-ask spreads
- Routes to cheapest exchange (LSE vs XETRA vs SIX)
- Adjusts for FX costs (GBP/EUR/USD)

**Result**: Gets the best possible price, minimizes slippage.

---

## THE 5-MODE CLOCK (Round-the-Clock Trading)

### **Mode A: ASIA NIGHT (00:00-07:50 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Markets open: Tokyo (TSE), Hong Kong (HKEX), Sydney (ASX), Auckland (NZX)
Active subscriptions: 92 tickers from 20,000+ Asia universe (rotated every 5s)
Strategy: HotScanner (volatility-momentum)
Signal: Score > 70
Risk gate: 31 checks
Execution: Smart router (TWAP)
Example: "Tokyo stock XYZ spiked on volume → HotScanner scores 75 → RiskGate approves → Buy 20 shares over 15min"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### **Mode AUCTION: OPENING (07:50-08:00 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LSE opening auction (wide spreads, limit orders only)
ACTION: NO NEW ENTRIES
ONLY: Manage existing positions, exit if needed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### **Mode B: EUROPEAN DAY (08:00-14:30 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Markets open: London (LSE), Frankfurt (XETRA), Paris (Euronext)
Active subscriptions: 92 tickers from 20,000+ Europe universe (rotated every 5s)
Strategies:
  - VanguardSniper (momentum on Vanguard tickers — continuous)
  - RotationScanner (sector rotation on Apex tickers — 60s snapshots)
Signals: Both can fire simultaneously
Risk gate: 31 checks (applies to both)
Execution: Smart router
Example: "VanguardSniper scores 75 on LSE/Tech AND RotationScanner scores 80 on sector rotation → Both approved → Buy 15 shares + Buy 10 shares"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### **Mode B+: US OVERLAP (14:30-16:30 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Markets open: NYSE/NASDAQ opens (New York)
Active subscriptions: 80 LSE + 20 US equities (100-line limit)
Strategies: All 3 (VanguardSniper + HotScanner + RotationScanner)
Risk gate: 31 checks (+ additional FX checks for US)
Execution: Smart router (+ currency conversion)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### **Mode AUCTION: CLOSING (16:30-16:35 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LSE closing auction
ACTION: NO NEW ENTRIES
ONLY: Close positions or let them run into carry
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### **Mode CARRY: OVERNIGHT (16:35-23:45 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All markets closed
ACTION: HOLD overnight positions
PROTECTION: Freeze Chandelier stops at session close to prevent gap hunts
Example: "We own 20 shares of TSL3.L with stop at £95. Market closes at 16:30. At 16:31, stop is FROZEN at £95 (can't move). At 00:00 next day, stop unfrozen and can move."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### **Mode DARK: LEARNING (21:00-23:00 UTC)**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ouroboros nightly calibration (NO TRADING)
What happens:
1. Ingest all today's trades from WAL
2. Calculate Bayesian win rate per signal type
3. Calculate DSR (Differentially Sharpe Ratio)
4. Run Kelly accelerator (update position sizing)
5. Calibrate Chandelier exit multipliers
6. Track information coefficient (alpha decay)
7. Apply alpha sieve (lock bad tickers)
8. Fit GARCH(1,1) for tomorrow's vol forecast
9. Write dynamic_weights.toml (position sizing for tomorrow)
10. Write universe_classification.toml (which tickers are Vanguard vs Apex)

Result: At 23:00 when Mode A opens, robot uses today's learned calibration.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## THE SUBSCRIPTION ROTATION MAGIC

### The Problem
```
IBKR API limit: Max 100 concurrent L1 market data subscriptions
Desired universe: 20,000+ tickers across 6 exchanges
Naive solution: Pick 100 best, lock forever → miss 99% of opportunities
```

### The Smart Solution
```
Every 5 seconds:
┌─────────────────────────────────────────┐
│ 1. Evaluate all 20,000 candidates by:   │
│    - Conviction score (from AI)         │
│    - Current volatility (hot tickers)   │
│    - Sector momentum (rotation)         │
│    - Information coefficient (alpha)    │
│                                         │
│ 2. Rank candidates 1-20,000            │
│                                         │
│ 3. Drop worst 100 subscriptions         │
│    Subscribe to best new 100            │
│    (1-second atomic swap)               │
│                                         │
│ 4. Process ticks for new batch         │
│    Send to HotScanner/RotationScanner   │
│                                         │
│ 5. Wait 5 seconds, repeat               │
└─────────────────────────────────────────┘
```

### Impact
```
Old: 100 static tickers × 2 strategies × 8 hours = ~800 opportunities/day
New: 20,000 candidates × (rotations/day) = ~880,000 candidate evaluations/day

BUT: Only 100 subscribed at any moment (respecting IBKR limit)
RESULT: Access to 200x more assets while staying within API constraints
```

---

## THE COMPLETE DATA FLOW (From Tick to Trade)

```
┌─────────────────────────────────────────────────────────────┐
│ IBKR Market Data (Real L1 bid/ask + 5-sec OHLCV bars)      │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Universe Router                                             │
│ - Route tick to Vanguard (continuous) OR Apex (60s snap)   │
│ - Apply 6 filters (amihud, aser, erroneous, halt, etc)    │
│ - Student-t Kalman smoothing (reject spoofed quotes)       │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌────────┐    ┌──────────┐   ┌────────────┐
   │Vanguard│    │HotScanner│   │ Rotation   │
   │Sniper  │    │(Mode A)  │   │ Scanner    │
   │(ADX+EMA│    │Volatility│   │ (Mode B)   │
   │ + RVOL)│    │ Momentum │   │ Sector Rot │
   └────┬───┘    └─────┬────┘   └──────┬─────┘
        │              │               │
        └──────────────┼───────────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │ Kelly 12-Factor Sizing │
          │ (Position sizing)      │
          └────────────┬───────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │ RiskGate 31 Checks     │
          │ (EVT, leverage, liquidity)
          └────────────┬───────────┘
                       │
          ┌────────────▼───────────┐
          │ ✓ APPROVED             │
          │ ✗ BLOCKED (risk too high)
          └────────────┬───────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │ SmartRouter TWAP       │
          │ (Break into 10 orders, │
          │  spread over 15 min)   │
          └────────────┬───────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │ IBKR Broker            │
          │ Submit BUY/SELL order  │
          └────────────┬───────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │ WAL Writer (append event)
          │ + ExitEngine (set stop) │
          │ + Telemetry (log trade)│
          └────────────────────────┘
```

---

## WHEN THINGS GO WRONG (Safe Failure)

### Scenario 1: Power Loss During Ouroboros
```
BEFORE (old system): TOML file half-written, corrupted, next day uses garbage calibration
AFTER: fs::write() + sync_all() ensures atomic writes. Either fully written or not at all.
RESULT: Next day uses yesterday's calibration (correct, safe)
```

### Scenario 2: Reconciliation Divergence
```
BEFORE: Robot silently "fixes" the mismatch, keeps trading, you never know
AFTER:
- Divergence detected → log to ReconcileAuditLog
- HALT trading immediately
- Require manual unlock: engine.manual_clear_reconcile_halt()
- You investigate the real problem before trading resumes
RESULT: Zero silent failures, audit trail of what went wrong
```

### Scenario 3: Mode Transition Subscription Swap Fails
```
BEFORE: Mode A → B transition fails, 50 tickers unsubscribed, 50 not subscribed
AFTER: SubscriptionManager has 5-min reconciliation vs IBKR
- Detects the failure automatically
- Auto-retries the swap
- Logs divergence to WAL
RESULT: Subscription mismatches self-heal
```

### Scenario 4: Market Crash / VIX Spike
```
BEFORE: Robot keeps trading normal position sizes, losses mount
AFTER:
- Cross-asset macro detects VIX spike
- Kelly formula automatically sizes 50% smaller
- Risk arbiter applies regime scaling
- Positions shrink, losses bounded
RESULT: Auto-scaling protects capital in chaos
```

---

## THE NIGHTLY LEARNING LOOP (Ouroboros)

Every night at 21:00-23:00 UTC:

```
┌─────────────────────────────────────────────────────────┐
│ Step 0: WAL Ingest                                      │
│ Read all today's OrderSubmitted/OrderFilled events      │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 1: Bayesian Win Rate                               │
│ Calculate: (wins + prior) / (trades + prior_strength)   │
│ Example: 12 wins, 20 trades → 12+1 / 20+10 = 54.5%     │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 2: Differentially Sharpe Ratio (DSR)              │
│ Measure: How much better than buy-and-hold?            │
│ High DSR = strategy has skill, not luck                 │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 3: Kelly Accelerator                               │
│ Blend: Yesterday's kelly + Today's new evidence         │
│ (Exponential weighted average, smooth updates)          │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 4: Exit Calibration (Chandelier Tuning)           │
│ Analysis: How often did we hit each profit rung?        │
│ If 60% of trades hit rung 5 → loosen multiplier        │
│ If 10% hit rung 5 → tighten multiplier                 │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 5: Regime Hunting (Hidden Markov Model)           │
│ Detect: Are we in bull-quiet / bull-volatile / etc?    │
│ Output: Regime classification for tomorrow              │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 6: Alpha Sieve (Information Coefficient)           │
│ Track: Is this ticker still predictable? (IC trending?) │
│ If IC < 0 for 20 days → lock ticker, don't trade it    │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 7: GARCH(1,1) Calibration                         │
│ Fit: Today's vol → forecast tomorrow's volatility      │
│ Used by Kelly formula (position sizing)                │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 8: QM-1 EVT (Extreme Value Theory)                │
│ Fit: GARCH residuals → GPD tail → VaR estimates       │
│ Result: Better black swan prediction                   │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 9: TOML Writer (Write Results)                    │
│ Output: dynamic_weights.toml (kelly, regime, etc)      │
│ Output: universe_classification.toml (Vanguard vs Apex)│
│ Guarantee: fsync_all() ensures no corruption           │
└──────────────────┬──────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────┐
│ Step 10: FX Rate Refresh                                │
│ Fetch: Latest GBP/USD, GBP/EUR, USD/JPY rates          │
│ Update: Used by SmartRouter for cost calculations       │
└──────────────────┬──────────────────────────────────────┘
                   ▼
           ✅ DONE AT 23:00
           Robot loads new calibration
           Mode A opens, trading resumes with updated weights
```

---

## EXAMPLE: COMPLETE TRADE FROM START TO FINISH

### Time: 09:30 UTC (Mode B, European trading)

```
TICK ARRIVES: XETRA stock "SMT" closes at €102.50

1. TICK PROCESSING
   └─ Universe router: Is SMT Vanguard or Apex?
      └─ Check: universe_classification.toml says SMT = Apex
      └─ Action: Buffer 60-second OHLCV snapshot

2. SIGNAL GENERATION (After 60 seconds)
   └─ RotationScanner evaluates SMT with 59 other Finance sector tickers
      └─ Tech sector return: +2.1% (above market average)
      └─ Finance sector return: +2.5% (outperforming!)
      └─ SMT is strongest Finance ticker
      └─ Score: 78 (high conviction)

3. KELLY SIZING
   └─ Yesterday's calibration (from Ouroboros):
      └─ RotationScanner win rate: 52%
      └─ RotationScanner avg win: 0.8%, avg loss: 0.9%
      └─ Kelly fraction: 0.045 (4.5% of equity)
      └─ Current equity: £10,000
      └─ Position size: £450 = ~4.4 shares at €102.50

4. RISK GATE (31 CHECKS)
   Check 1: VaR Check (EVT model)
      └─ Max loss if market crashes 10%: £45 (0.45% of equity) ✓
   Check 2: Leverage Check
      └─ Current leverage: 1.2x (within 2x limit) ✓
   Check 3: Drawdown Check
      └─ Current daily drawdown: -0.8% (within 2.5% limit) ✓
   ...
   Check 31: Spread Check
      └─ Current bid-ask: €0.02 (0.02%, tight) ✓

   Result: ✅ ALL PASS → TRADE APPROVED

5. SMART ROUTER (TWAP EXECUTION)
   └─ Order: Buy 4 SMT shares
      └─ Break into 10 orders of 0.4 shares each
      └─ Spread over 15 minutes (every 90 seconds)
      └─ Submit order 1: 0.4 @ limit €103.00
      └─ Wait 90s
      └─ Submit order 2: 0.4 @ limit €103.05
      └─ Wait 90s
      └─ ... (continue)
      └─ Last order submitted at ~09:45 UTC
      └─ Filled by ~09:50 UTC

6. WAL APPEND
   └─ Event: OrderSubmitted(ticker=SMT, qty=4, side=Buy, strategy=RotationScanner)
   └─ Event: OrderFilled(order_id=xxx, filled_qty=4, avg_price=€102.48)
   └─ Write to /app/events/2026-03-14.ndjson
   └─ Sync to disk (fsync_all guarantees no loss)

7. EXIT ENGINE
   └─ Set Chandelier stop at €100.50 (5-ATR trailing)
   └─ Profit targets (5-rung ladder):
      └─ Rung 1: Sell 1 share at €103.50 (£15 profit)
      └─ Rung 2: Sell 1 share at €104.50 (£30 profit)
      └─ Rung 3: Sell 1 share at €105.50 (£45 profit)
      └─ Rung 4: Sell 1 share at €107.00 (£60 profit)
      └─ Rung 5: Sell 1 share at €109.00 (£80 profit)

8. TELEMETRY
   └─ Log: signal_count += 1
   └─ Log: trade_count += 1
   └─ Log: active_positions += 1
   └─ Dashboard updates at next 10s interval

---

OUTCOME OPTIONS:

A) Trade works: SMT rallies to €107
   └─ Rung 3 triggered, sell at €105.50
   └─ Profit: £15 + £30 + £45 = £90 (0.9% of equity)
   └─ Next night: Ouroboros notes win, increases Kelly fraction slightly

B) Trade fails: SMT drops to €100
   └─ Chandelier stop triggered at €100.50
   └─ Loss: £10 (0.1% of equity)
   └─ Next night: Ouroboros notes loss, Kelly fraction adjusts slightly down

C) Market crashes: VIX spikes to 40
   └─ Cross-asset macro detects spike
   └─ Kelly formula sizes new orders 50% smaller
   └─ Existing position's stop remains frozen (wait for recovery)
```

---

## BOTTOM LINE: WHAT YOU GET AFTER PHASES 0-25

**Before**: A UK-only day trader on 100 LSE ETPs, 8 hours/day, 1 strategy.

**After**: A global 24/5 automated hedge fund with:

✅ **Scope**:
- Access to 20,000+ tickers via smart rotation (6 exchanges)
- 22-hour daily trading (5 modes: Asia, Europe, US, Carry, Learning)
- 2 independent strategies (volatility + sector rotation)
- 100+ paper trades under the belt (validated)

✅ **Safety**:
- Crash-proof data (fsync guarantees)
- Audit trails (reconciliation locks, no silent failures)
- Auto-scaling (Kelly adapts to volatility)
- Safe failure modes (circuit breaker, panic guard, watchdog)

✅ **Learning**:
- Nightly Ouroboros calibration (Bayesian, Kelly, exit tuning)
- Information coefficient tracking (alpha decay detection)
- Regime detection (bull-quiet, bull-volatile, bear-quiet, bear-volatile)
- GARCH vol forecasting (tomorrow's volatility predicted today)

✅ **Math**:
- QM-1: EVT for black swan modeling
- QM-2: Hayashi-Yoshida for async correlation
- QM-3: Log-Thompson for momentum learning
- QM-4: Student-t Kalman for spoofed quote rejection

✅ **Execution**:
- TWAP order routing (15-min execution to minimize slippage)
- Smart FX hedging (GBP/USD/EUR cost optimization)
- 31 risk checks before every trade
- 5-rung Chandelier exit (profit ladder)

✅ **Operations**:
- Live terminal dashboard (trade count, P&L, regime, health)
- Watchdog (auto-restart on hang)
- Dead letter queue (failed order recovery)
- Daily S3 backups (disaster recovery)

**Expected Performance**:
- Win rate: ≥ 40% (industry standard is 30%)
- Sharpe ratio: ≥ 0.5 (positive risk-adjusted return)
- Daily return: 0.3-0.8% (3-8% annualized, compounded)
- Max drawdown: < 8% (hard stop at 2.5%)
- Zero silent failures (everything auditable)

**Timeline**:
- Phases 0-8: 7.5 + 77 = **84.5 hours** (3-4 weeks)
- Phases 9-15: ~120 hours (4-5 weeks)
- Phases 16-21: ~110 hours (4-5 weeks)
- Phases 22-23: ~87 hours (3-4 weeks)
- **Total: 451 hours at 30h/week = 3.5 months**
- **Live capital target: Late June 2026**

---

**Status**: COMPLETE PLAN READY FOR EXECUTION
**Next**: Execute Phase 0 (blockers) today
**Then**: Phases 1-25 sequentially
