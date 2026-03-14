# AEGIS V2 — PHASE DEPENDENCIES & GATE CRITERIA

## Phase Dependency Graph

```
┌─────────────────────────────────────────────────────────────────┐
│  FOUNDATION (Phase 0-2: Complete)                              │
│  - Core engine loop                                             │
│  - IB Gateway connector                                         │
│  - Redis persistence                                           │
│  - Base data structures                                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  Phase 3-6       │ │  Phase 24        │ │  (Parallel)      │
│  WIRING (4.5h)   │ │  QUANTUM APEX    │ │  Can start any   │
│  ✓ Python Brain  │ │  (10h)           │ │  time after P0-2  │
│  ✓ ModeBPlus     │ │  ✓ FFI Bridge    │ │                  │
│  ✓ Rotation Base │ │  ✓ DQN Weights   │ │                  │
│  ✓ 5 Tests       │ │  ✓ Hawkes Order  │ │                  │
│  Gate: 565 tests │ │  ✓ 5 Tests       │ │                  │
└────────┬─────────┘ │  Gate: C++ bridge│ │                  │
         │           │  compiles OK     │ │                  │
         │           └──────┬───────────┘ │                  │
         │                  │             │                  │
         └──────────────────┼─────────────┘                  │
                            │                                │
                            ▼                                │
                  ┌─────────────────────┐                    │
                  │  Phase 7 (15h)      │                    │
                  │  SUBSCRIPTION       │                    │
                  │  ROTATION           │                    │
                  │  ✓ 3 regions        │                    │
                  │  ✓ 5-sec cycles     │                    │
                  │  ✓ 20k coverage     │                    │
                  │  Gate: rotating OK  │                    │
                  └────────┬────────────┘                    │
                           │                                 │
                           ▼                                 │
                  ┌─────────────────────┐                    │
                  │  Phase 8 (77h)      │                    │
                  │  PRE-CONDITIONS     │                    │
                  │  ✓ 33 modules gated │                    │
                  │  ✓ Price/Vol/Vol    │                    │
                  │  ✓ Time/Macro gates │                    │
                  │  Gate: all 33 gated │                    │
                  └────────┬────────────┘                    │
                           │                                 │
                           ▼                                 │
                  ┌─────────────────────┐                    │
                  │  Phase 9 (20h)      │                    │
                  │  CROSS-ASSET MACRO  │                    │
                  │  ✓ VIX, DXY, Credit │                    │
                  │  ✓ F&G Index        │                    │
                  │  ✓ Signal weighting │                    │
                  │  Gate: macro live   │                    │
                  └────────┬────────────┘                    │
                           │                                 │
           ┌───────────────┴───────────────┐                │
           │                               │                │
           ▼                               ▼                │
  ┌──────────────────┐        ┌──────────────────┐         │
  │  Phases 10-15    │        │  (Parallel)      │         │
  │  (120h total,    │        │  Can start as    │         │
  │  4h each module) │        │  modules ready   │         │
  │                  │        │                  │         │
  │  ✓ Momentum (6)  │        │  Module depends: │         │
  │  ✓ MeanRev (6)   │        │  - Phase 8 gates │         │
  │  ✓ Volatility(6) │        │  - Phase 9 macro │         │
  │  ✓ CrossAsset(6) │        │                  │         │
  │  ✓ ML (6)        │        │  Start module N  │         │
  │  ✓ OrderFlow (3) │        │  once gates done │         │
  │                  │        │                  │         │
  │ Gate: 95%+ tests │        │                  │         │
  │ per module       │        │                  │         │
  └────────┬─────────┘        └──────────────────┘         │
           │                                                │
           └──────────────────┬───────────────────────────┬─┘
                              │                           │
                              ▼                           │
                     ┌──────────────────────┐            │
                     │  Phase 16 (52h)      │            │
                     │  OUROBOROS LEARNING  │            │
                     │  ✓ Data collection   │            │
                     │  ✓ Training (DQN)    │            │
                     │  ✓ Validation        │            │
                     │  ✓ Model snapshot    │            │
                     │  Gate: 2h deadline   │            │
                     │  met, convergence OK │            │
                     └──────────┬───────────┘            │
                                │                        │
                                ▼                        │
                     ┌──────────────────────┐            │
                     │  Phase 17 (18h)      │            │
                     │  TELEMETRY DASH      │            │
                     │  ✓ HTTP API          │            │
                     │  ✓ WebSocket stream  │            │
                     │  ✓ Snapshot complete │            │
                     │  Gate: <100ms latency│            │
                     └──────────┬───────────┘            │
                                │                        │
           ┌────────────────────┴──────────┐             │
           │                               │             │
           ▼                               ▼             │
  ┌──────────────────────┐      ┌──────────────────┐    │
  │  Phases 18-21        │      │  (Parallel)      │    │
  │  (80h, 20h each)     │      │  Can start as    │    │
  │                      │      │  exchanges ready │    │
  │  ✓ TSE (Japan)       │      │                  │    │
  │  ✓ HKEX (HK)         │      │ Exchange depends │    │
  │  ✓ ASX (Australia)   │      │ - Phase 7 rotation   │
  │  ✓ Euronext/US       │      │ - Macro available    │
  │                      │      │                  │    │
  │ Gate: 22h trading    │      │ Start exchange N │    │
  │ verified, 4 live     │      │ once rotation +  │    │
  │                      │      │ macro ready      │    │
  └──────────┬───────────┘      └──────────────────┘    │
             │                                          │
             └──────────────────┬───────────────────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │  Phase 22 (47h)      │
                     │  HARDENING           │
                     │  ✓ PnL tracking      │
                     │  ✓ Audit trail       │
                     │  ✓ Kill switch       │
                     │  ✓ Circuit breaker   │
                     │  Gate: audit complete│
                     │  kill switch <100ms  │
                     └──────────┬───────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │  Phase 25 (20h)      │
                     │  LIVE DEPLOYMENT     │
                     │  ✓ £1k → £10k scaling│
                     │  ✓ Risk limits       │
                     │  ✓ Monitoring        │
                     │  Gate: £10k deployed │
                     │  7+ days profitable  │
                     └──────────────────────┘
```

---

## DETAILED GATE CRITERIA BY PHASE

### Phase 3-6: WIRING (4.5h)
**Dependencies**: Phase 0-2 complete
**Gate Criteria**:
- [ ] ✓ 565+ tests passing
- [ ] ✓ `apex_snapshot` enum added to types
- [ ] ✓ `SessionMode::ModeBPlus` variant exists
- [ ] ✓ SubscriptionManager mode transitions on `compute_mode='apex'`
- [ ] ✓ Trading halts at 23:00 UTC daily (logged)
- [ ] ✓ Python brain receives/processes apex_snapshot JSON
- [ ] ✓ Acceptance tests (5 tests): Mode A, Mode B, 23:00 halt, ModeBPlus, reconcile
- [ ] ✓ Zero warnings in `cargo build --release`

**Blocking Issues**: None (parallel with Phase 24)

---

### Phase 24: QUANTUM APEX (10h)
**Dependencies**: Phase 0-2 complete, C++ compiler available
**Gate Criteria**:
- [ ] ✓ C++ bridge compiles with zero warnings
- [ ] ✓ FFI bindings exported correctly (DQN weights, Hawkes predictions)
- [ ] ✓ DQN training converges (loss < 0.01 over 100 iterations)
- [ ] ✓ Neural Hawkes RMSE < 5% of realized volatility
- [ ] ✓ 5 comprehensive tests passing (FFI, DQN, Hawkes, integration)
- [ ] ✓ Cargo linking works: `cargo build --release` completes
- [ ] ✓ Docker build includes C++ binary successfully

**Blocking Issues**: C++ build environment must be available on EC2

---

### Phase 7: SUBSCRIPTION ROTATION (15h)
**Dependencies**: Phase 3-6 complete, Phase 24 optional but not blocking
**Gate Criteria**:
- [ ] ✓ SubscriptionRotation::try_rotate() round-robins through 20k universe
- [ ] ✓ 5-second interval timer accurate (±100ms)
- [ ] ✓ 3 regions (Asia, Europe, US) rotate independently
- [ ] ✓ Each region: 100 subscriptions max at any time
- [ ] ✓ Region coverage: 6,667 tickers each (20,000 ÷ 3)
- [ ] ✓ 200+ rotation cycles per day per region (30,600 sec ÷ 5 sec)
- [ ] ✓ Rotation events logged with cycle counter
- [ ] ✓ 5 unit tests passing (round-robin, intervals, coverage, cycles, stats)
- [ ] ✓ Zero subscription overlap between regions
- [ ] ✓ Metrics exported: `total_rotations`, `coverage_pct` per region

**Blocking Issues**: None (depends only on Phase 3-6)

---

### Phase 8: PRE-CONDITIONS & WIRING (77h)
**Dependencies**: Phase 7 complete, Phase 9 recommended (but can work with defaults)
**Gate Criteria**:
- [ ] ✓ PreConditionValidator initialized
- [ ] ✓ All 33 modules registered in validator
- [ ] ✓ Custom gates per module:
  - [ ] Price bounds (e.g., 0.01-100k)
  - [ ] Volume thresholds (20-SMA, daily minimum)
  - [ ] Volatility gates (ATR %, IV percentile)
  - [ ] Time-of-day gates (trading hours per module)
  - [ ] Macro gates (VIX max, credit spread max)
  - [ ] Emergency circuit breaker (2% daily loss, 0.5% hourly)
- [ ] ✓ 7+ pre-condition tests passing
- [ ] ✓ Validation logic prevents invalid tickers (zero false positives)
- [ ] ✓ Valid tickers pass validation (zero false negatives)
- [ ] ✓ Documentation per module (what conditions it requires)

**Blocking Issues**: None (Phase 9 can run in parallel)

---

### Phase 9: CROSS-ASSET MACRO (20h)
**Dependencies**: Phase 8 complete (to gate on macro values)
**Gate Criteria**:
- [ ] ✓ VIX fetched every 60 seconds, range 10-60 realistic
- [ ] ✓ DXY fetched with 20-SMA tracking
- [ ] ✓ Credit spreads separated (HY OAS + IG OAS)
- [ ] ✓ Fear & Greed Index with 5 labels (extreme_fear → extreme_greed)
- [ ] ✓ MacroSnapshot struct captures all 4 indicators
- [ ] ✓ MacroSignalWeighter computes -1..1 signal (risk-off to risk-on)
- [ ] ✓ Signal weighting logic correct:
  - [ ] VIX high → negative signal (risk-off)
  - [ ] DXY strong → negative signal (capital flight)
  - [ ] Credit wide → negative signal (stress)
  - [ ] F&G low → negative signal (fear)
- [ ] ✓ Macro signal modulates base module signals correctly
- [ ] ✓ PreConditionValidator.update_macro_state() called every 60sec
- [ ] ✓ 4 comprehensive macro integration tests passing
- [ ] ✓ Macro data sourced (real API or mock with consistent values)

**Blocking Issues**: None (can mock data if real APIs unavailable)

---

### Phases 10-15: 33 MODULE INTEGRATION (120h)
**Dependencies**: Phase 8 (gates), Phase 9 (macro)
**Gate Criteria (per module)**:
- [ ] ✓ Module compiles without warnings
- [ ] ✓ Implements `process_bar()` → `SignalStrength` enum
- [ ] ✓ Implements `exit_signal()` → bool
- [ ] ✓ SignalStrength enum: Long, LongWeak, Short, ShortWeak, None
- [ ] ✓ 5-7 unit tests passing (signal generation, pre-condition blocking, macro modulation)
- [ ] ✓ Code coverage 95%+
- [ ] ✓ Pre-condition gates block invalid tickers
- [ ] ✓ Macro signal modulates output (risk-on/off)
- [ ] ✓ Documentation: What does this module trade? Example signals?

**All 33 Modules Gate**:
- [ ] ✓ 165+ tests total (5 per module × 33 modules)
- [ ] ✓ All modules registered in pre-condition validator
- [ ] ✓ Signal blending works (can combine outputs)
- [ ] ✓ No circular dependencies between modules

**Blocking Issues**: None (can implement modules in any order once Phase 8-9 complete)

---

### Phase 16: OUROBOROS NIGHTLY LEARNING (52h)
**Dependencies**: Phases 10-15 complete (need trading data)
**Gate Criteria**:
- [ ] ✓ TradeCollector gathers EOD trades from Redis/trading log
- [ ] ✓ Labeling: 100% of trades labeled (win/loss/breakeven)
- [ ] ✓ Feature extraction: 50+ features per trade
- [ ] ✓ EnsembleTrainer trains DQN (loss < 0.1)
- [ ] ✓ EnsembleTrainer trains Neural Hawkes (loss < 0.15)
- [ ] ✓ BacktestValidator runs on last 5 days (< 60 minutes)
- [ ] ✓ A/B test: new models vs old (no degradation > 2%)
- [ ] ✓ Model snapshot uploaded to S3 daily
- [ ] ✓ 10-step pipeline logs each step
- [ ] ✓ **HARD DEADLINE**: 23:50 ET start, 02:00 ET finish (2-hour window, 7,200 seconds)
  - Step 1: Collect (30s)
  - Step 2: Label (30s)
  - Step 3-4: Train (60s)
  - Step 5: Validate (60s)
  - Step 6-7: Optimize (30s)
  - Step 8: Snapshot (30s)
  - Step 9: A/B test (30s)
  - Step 10: Alert (10s)
  - **Total: ~280s, must complete well before 2-hour deadline**
- [ ] ✓ Daily stats logged (# trades, win rate, total PnL)
- [ ] ✓ Performance tracking vs previous day (improvement or degradation flagged)
- [ ] ✓ Email alert on deadline miss or performance drop > 5%
- [ ] ✓ 8+ comprehensive tests passing

**Blocking Issues**: Need 50+ trades daily to train (may need paper trading period first)

---

### Phase 17: TELEMETRY DASHBOARD (18h)
**Dependencies**: Phases 10-15 complete
**Gate Criteria**:
- [ ] ✓ HTTP GET `/telemetry/latest` returns complete snapshot (JSON)
- [ ] ✓ Snapshot includes:
  - [ ] Timestamp (UTC)
  - [ ] 33 module signals (-1..1)
  - [ ] Current positions (ticker, entry, current price, PnL)
  - [ ] Macro state (VIX, DXY, credit, F&G)
  - [ ] Rotation stats (coverage %, subscriptions per region)
  - [ ] Daily/hourly PnL
- [ ] ✓ WebSocket `/telemetry/ws` streams updates every 5 seconds
- [ ] ✓ Latency < 100ms (from engine update to client receive)
- [ ] ✓ Frontend loads in < 1 second
- [ ] ✓ No data stale > 10 seconds
- [ ] ✓ 3 comprehensive tests passing (GET, WS, snapshot completeness)
- [ ] ✓ Can handle 100+ concurrent WebSocket connections

**Blocking Issues**: None (can mock position/signal data if modules not complete)

---

### Phases 18-21: MULTI-EXCHANGE (80h, 20h each)
**Dependencies**: Phase 7 (rotation must work), Phase 9 (macro must work)
**Gate Criteria (per exchange)**:
- [ ] ✓ Exchange struct initialized (name, timezone, trading hours)
- [ ] ✓ Ticker universe loaded (2,000-5,000 per exchange)
- [ ] ✓ Trading hours validated (time-zone conversion accurate ±1 second)
  - [ ] LSE: 08:00-16:30 UTC
  - [ ] TSE: 00:00-07:00 UTC (09:00-16:00 JST)
  - [ ] HKEX: 01:00-08:00 UTC (09:00-16:00 HKT)
  - [ ] ASX: 22:00 prev-06:00 UTC (10:00 prev-16:00 AEDT)
  - [ ] Euronext: 07:00-17:00 UTC
  - [ ] NYSE/NASDAQ: 13:00-21:00 UTC
- [ ] ✓ Rotation manager per exchange
- [ ] ✓ No overlapping subscriptions across regions/exchanges
- [ ] ✓ 20+ integration tests per exchange
- [ ] ✓ 22-hour continuous trading verified (all 6 exchanges combined)
- [ ] ✓ Pre-conditions gating works per exchange
- [ ] ✓ Macro gates apply uniformly across all exchanges

**All 6 Exchanges Gate**:
- [ ] ✓ 20,000+ global ticker coverage verified
- [ ] ✓ No subscription contention (IB Gateway limit not exceeded)
- [ ] ✓ Time-zone handling correct (no trades outside trading hours)
- [ ] ✓ Seamless handoff between exchanges (no gaps in 22-hour window)
- [ ] ✓ 80 tests total passing

**Blocking Issues**: IB Gateway must support multi-region subscriptions

---

### Phase 22: INSTITUTIONAL HARDENING (47h)
**Dependencies**: Phases 10-21 complete
**Gate Criteria**:
- [ ] ✓ DailyPnLTracker generates CSV + JSON reports
- [ ] ✓ Reports include: date, trades, wins, losses, win rate, total PnL, best/worst
- [ ] ✓ AuditTrail logs 100% of events (orders, cancels, liquidations, halts, errors)
- [ ] ✓ Audit trail format: JSONL (one event per line)
- [ ] ✓ KillSwitch activates in < 100ms
- [ ] ✓ KillSwitch reason logged and exposed via API
- [ ] ✓ Circuit breaker halts trading on 2% daily loss
- [ ] ✓ Circuit breaker halts trading on 0.5% hourly loss
- [ ] ✓ Compliance record keeping (10 years retention)
- [ ] ✓ PnL reporting accurate to pence (no rounding errors)
- [ ] ✓ Position reconciliation (IB API vs Redis) every 5 minutes
- [ ] ✓ Reconciliation alerts on mismatch > £1
- [ ] ✓ 6+ hardening tests passing

**Blocking Issues**: None (can integrate incrementally)

---

### Phase 25: LIVE CAPITAL DEPLOYMENT (20h)
**Dependencies**: All Phases 3-22 complete, 100+ paper trades logged
**Gate Criteria (cumulative across 4-week ramp)**:
- [ ] **Week 1 (£1,000 live)**:
  - [ ] Connectivity test (orders execute, fills received)
  - [ ] Position management (can open/close)
  - [ ] Withdrawal test (can withdraw 10% of capital)
  - [ ] PnL accuracy (matches IB account statement)
  - [ ] No system crashes (uptime 99.9%)
  - [ ] Gate: 7 days complete

- [ ] **Week 2 (£2,500 live)**:
  - [ ] Cumulative PnL > 0% (breakeven minimum)
  - [ ] Win rate 45%+
  - [ ] Max drawdown < 3% (£75 on £2,500)
  - [ ] Daily PnL > £1 (0.04%)
  - [ ] Gate: 7 days profitable

- [ ] **Week 3 (£5,000 live)**:
  - [ ] Cumulative PnL > 2% (£100+)
  - [ ] Win rate 45%+
  - [ ] Sharpe ratio > 1.0
  - [ ] Max drawdown < 5% (£250)
  - [ ] Gate: 7 days, no degradation

- [ ] **Week 4+ (£10,000 live)**:
  - [ ] Cumulative PnL > 3% (£300+)
  - [ ] Win rate 45%+
  - [ ] Sharpe ratio > 1.5
  - [ ] Max drawdown < 8% (£800)
  - [ ] Daily PnL > £3 (0.3%)
  - [ ] Gate: indefinite compound growth

**Final Success Criteria**:
- [ ] ✓ £10k deployed
- [ ] ✓ 7+ consecutive profitable days
- [ ] ✓ 0.3-0.8% daily (£3-8 on £10k)
- [ ] ✓ Zero catastrophic losses (> 10% in single day)
- [ ] ✓ Sharpe ratio > 1.5
- [ ] ✓ All safety systems functional

---

## PARALLEL WORK OPPORTUNITIES

### Can Start Immediately (After Phase 0-2)
- Phase 24: Quantum Apex (independent C++ work)
- Phase 9: Macro integration (can use mock data)
- Phase 17: Telemetry dashboard (can mock engine data)

### Can Start After Phase 7
- All 6 exchanges (Phases 18-21) can run in parallel

### Can Start After Phase 8
- All 33 modules (Phases 10-15) can implement in parallel
- Each developer takes 3-5 modules

### Must Run Sequentially
- Phase 3-6 → Phase 7 → Phase 8 → Phase 9
- Phase 16 depends on 10+ days of trading data
- Phase 25 depends on phases 3-22 all passing gates

---

## RISK MITIGATION

### If Phase Fails Gate
1. **Root cause analysis**: Which test failed? Why?
2. **Don't proceed to next phase**: Gates exist for a reason
3. **Fix in current phase**: Debug, add tests, verify
4. **Re-verify gate**: No shortcuts
5. **Document issue**: Add to lessons learned

### If Deadline Missed
- Ouroboros deadline (Phase 16): halt all trading next day
- Other deadlines: slip the timeline, don't cut corners
- Add additional QA time (100 hours budgeted)

### If Performance Degrades
- Phase 25 live deployment gates include performance checks
- If daily PnL < 0.3% for 3 consecutive days, revert to paper
- Run Ouroboros validation (A/B test) to identify regression
- Fix in code, re-test, then resume live trading

---

## SIGN-OFF TEMPLATE

### Phase Completion Sign-Off

```
Phase: [X] (Name)
Completed: [DATE]
Tester: [NAME]

Gate Criteria Verification:
- [ ] Criterion 1: PASS ✓
- [ ] Criterion 2: PASS ✓
- [ ] Criterion 3: PASS ✓
...

Issues Found: [NONE / list any]
Tests Passing: [N]/[TOTAL]
Code Coverage: [PCT]%
Documentation: [COMPLETE / INCOMPLETE]

Sign-Off: [SIGNATURE/APPROVAL]
Notes: [ANY BLOCKERS FOR NEXT PHASE]
```

---

**END OF DEPENDENCIES & GATES**
