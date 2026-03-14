# AEGIS V2 MASTER PLAN — QUICK START GUIDE

**Document**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/COMPLETE_MASTER_PLAN_1000H.md` (2,655 lines)

## Overview

This is your complete execution roadmap for building a global 22-hour trading robot that will:
- Trade 20,000+ tickers across 6 exchanges
- Run 33 independent trading modules + Quantum Apex neural weighting
- Learn nightly with Ouroboros (ML pipeline)
- Scale from £1k to £10k live capital

**Total effort**: 1,043 hours across 25 phases
**Timeline**: ~21 weeks at 20 hours/week
**Target outcome**: 0.3-0.8% daily (145-348% annualized)

---

## PHASES AT A GLANCE

### TODAY (Week 1) — 14.5 Hours
- **Phase 3-6** (4.5h): Wiring — Python Brain, ModeBPlus enum, rotation logic, 5 acceptance tests
- **Phase 24** (10h): Quantum Apex — C++ FFI bridge, DQN signal weighting, Neural Hawkes order flow
- **Gate**: 565+ tests passing, live on EC2

### WEEK 2 — 15 Hours
- **Phase 7**: SubscriptionManager Full Rotation — 5-second rotation through 20,000 tickers (3 regions × 100 subs)
- **Gate**: All 3 regions rotating independently

### WEEKS 3-4 — 77 Hours
- **Phase 8**: Pre-Conditions & 33 Module Wiring — Input validation gates for all 33 modules
- **Gate**: All 33 modules registered with price/volume/volatility/time/macro gates

### WEEK 5 — 20 Hours
- **Phase 9**: Cross-Asset Macro Integration — VIX, DXY, Credit spreads, Fear & Greed Index
- **Gate**: Macro signal working (-1 to +1), modulating module outputs

### WEEKS 6-10 — 120 Hours
- **Phases 10-15**: 33 Module Integration (4h each)
  - Momentum (6 modules): Breakout, Continuation, Reversal, Reaccumulation, Distribution, Fade
  - Mean Reversion (6 modules): Overbought, Oversold, Bandwidth, Z-Score, Keltner, Bollinger
  - Volatility (6 modules): Expansion, Contraction, Breakout, Range, Skew, Term
  - Cross-Asset (6 modules): Pair Trading, Correlation Fade, Macro Hedge, Index Constituent, Sector Rotation, Currency Carry
  - ML (6 modules): Meta-Label, Signal Blend, Ensemble, LSTM, XGBoost, Neural Network
  - Order Flow (3 modules): Imbalance, Toxicity, VWAP Hunt
- **Gate**: Each module 95%+ test coverage, 165+ tests total

### WEEKS 11-12 — 52 Hours
- **Phase 16**: Ouroboros Nightly Learning
  - 10-step ML pipeline: collect → label → train → validate → sweep → update DQN → retrain Hawkes → snapshot → A/B test → alert
  - Hard deadline: 2 hours (23:50 ET start, 02:00 ET finish)
- **Gate**: 2-hour deadline met, daily batch convergence (DQN loss < 0.1)

### WEEK 13 — 18 Hours
- **Phase 17**: Telemetry Dashboard
  - HTTP GET `/telemetry/latest` (full snapshot)
  - WebSocket `/telemetry/ws` (5-second streams)
  - 33 module signals, current positions, macro state, rotation stats, PnL
- **Gate**: <100ms latency, <1 second page load

### WEEKS 14-18 — 80 Hours
- **Phases 18-21**: Multi-Exchange Global (20h each)
  - Phase 18: Tokyo Stock Exchange (TSE) — 2,000 tickers, 09:00-16:00 JST
  - Phase 19: Hong Kong Exchanges (HKEX) — 3,000 tickers, 09:30-16:00 HKT
  - Phase 20: Australian Securities Exchange (ASX) — 2,500 tickers, 10:00-16:00 AEDT
  - Phase 21: Euronext + NYSE/NASDAQ — 5,000 tickers each
- **Gate**: 22-hour continuous trading verified, 4 new exchanges live

### WEEKS 19-20 — 47 Hours
- **Phase 22**: Institutional Hardening
  - PnL tracking & daily reports
  - 100% audit trail (every trade logged)
  - Kill switch (< 100ms response)
  - Circuit breaker (2% daily loss halt)
  - SEC-ready compliance record keeping
- **Gate**: Audit trail complete, kill switch functional

### WEEK 21 — 20 Hours
- **Phase 25**: Live Capital Deployment
  - Week 1: £1,000 (test connectivity)
  - Week 2: £2,500 (confirm reproducible)
  - Week 3: £5,000 (ramp up)
  - Week 4: £10,000 (full deployment)
- **Gate**: £10k deployed, daily PnL > 0.3%, max drawdown < 8%

---

## KEY FILE LOCATIONS

| File | Purpose | Lines | Status |
|------|---------|-------|--------|
| `rust_core/src/subscription_manager.rs` | 5-second rotation logic | ~300 | Phase 7 |
| `rust_core/src/preconditions.rs` | Pre-condition gates (all 33 modules) | ~400 | Phase 8 |
| `rust_core/src/macro_integrations.rs` | VIX/DXY/Credit/F&G fetcher | ~250 | Phase 9 |
| `rust_core/src/modules/` | 33 trading modules (separate files) | ~4,000 | Phases 10-15 |
| `src/ouroboros/` | ML pipeline (collector, trainer, validator, orchestrator) | ~800 | Phase 16 |
| `src/telemetry/` | WebSocket + REST API | ~400 | Phase 17 |
| `src/exchanges/` | 6 exchange adapters (LSE, TSE, HKEX, ASX, EU, US) | ~600 | Phases 18-21 |
| `src/compliance/` | PnL, audit trail, kill switch | ~300 | Phase 22 |
| `src/main.rs` | Engine orchestrator (integration loop) | ~800 | All phases |

---

## TEST STRUCTURE

Each phase includes **5-10 unit tests**:

### Phase 3-6 Tests (5 tests)
- Mode A transition
- Mode B transition
- 23:00 UTC halt
- ModeBPlus enum
- Reconcile halt

### Phase 7 Tests (5 tests)
- Round-robin rotation
- 5-second interval
- 3 regions independent
- Coverage 20k universe
- Daily rotation cycles

### Phase 8 Tests (7 tests)
- Price validation
- Volume validation
- Session mode gate
- Time-of-day gate
- Macro gate (VIX)
- Circuit breaker
- All 33 modules registered

### Phase 9 Tests (4 tests)
- VIX signal -1..1
- DXY trend detection
- Credit spread widening
- F&G mapping

### Phases 10-15 Tests (165 total, ~5 per module)
- Signal generation (Long, LongWeak, Short, ShortWeak, None)
- Pre-condition gating
- Macro modulation
- Exit logic

### Phase 16 Tests (8 tests)
- Trade collection
- Labeling (win/loss)
- Training convergence
- Validation backtest
- A/B test comparison
- Deadline deadline enforcement

### Phase 17 Tests (3 tests)
- HTTP GET `/telemetry/latest`
- WebSocket stream
- Snapshot completeness

### Phases 18-21 Tests (80 total, ~20 per exchange)
- Trading hour validation
- Time-zone conversion
- Ticker universe size
- Rotation independent per region
- No overlapping subscriptions

### Phase 22 Tests (6 tests)
- PnL report accuracy
- Audit trail logging
- Kill switch response
- Circuit breaker trigger
- Compliance record keeping

---

## EXECUTION CHECKLIST

### Before You Start
- [ ] Clone/sync latest code from EC2
- [ ] Verify Cargo builds without warnings
- [ ] Confirm 556 tests passing
- [ ] IB Gateway running on port 4004
- [ ] Redis running (password: nzt48redis)

### Phase 3-6 (Today)
- [ ] Add `apex_snapshot` enum to types.rs
- [ ] Implement `ModeBPlus` session mode variant
- [ ] Update SubscriptionManager mode transition
- [ ] Add 5 acceptance tests
- [ ] Deploy to EC2: `bash deploy.sh`
- [ ] Verify 565+ tests passing
- [ ] Check logs: `docker logs nzt48 --tail 50`

### Phase 7 (Week 2)
- [ ] Implement SubscriptionRotation struct
- [ ] Add 5-second interval timer
- [ ] Test round-robin through 20k universe
- [ ] Integrate into engine main loop
- [ ] Verify all 3 regions rotate independently

### Phase 8 (Weeks 3-4)
- [ ] Create preconditions.rs framework
- [ ] Register all 33 modules
- [ ] Set custom gates per module (price, volume, volatility, time, macro)
- [ ] Add circuit breaker logic
- [ ] Run 7+ pre-condition tests

### Continue with Phases 9-25...

---

## WEEKLY BURN RATE & TIMELINE

Assuming **20 hours/week dedication**:

| Week | Phase(s) | Hours | Cumulative | Status |
|------|----------|-------|-----------|--------|
| 1 | 3-6 + 24 | 40h | 40h | TODAY |
| 2 | 7 | 20h | 60h | ✓ rotating |
| 3-4 | 8 | 77h | 137h | ✓ gated |
| 5 | 9 | 20h | 157h | ✓ macro live |
| 6-10 | 10-15 | 120h | 277h | ✓ 33 modules |
| 11-12 | 16 | 52h | 329h | ✓ learning |
| 13 | 17 | 18h | 347h | ✓ dashboard |
| 14-18 | 18-21 | 80h | 427h | ✓ 6 exchanges |
| 19-20 | 22 | 47h | 474h | ✓ hardened |
| 21 | 25 | 20h | 494h | ✓ £10k live |
| 22-23 | Testing + QA | 100h | 594h | ✓ validation |
| 24 | Docs + Training | 80h | 674h | ✓ complete |

**Total: 674 hours ≈ 33 weeks ≈ 8 months at 20h/week**

(Or compress to 4-5 months at 40h/week)

---

## SUCCESS CRITERIA (End of Phase 25)

✅ **Performance**: 0.3-0.8% daily (£3-8 on £10k)
✅ **Win Rate**: 45%+ across all trades
✅ **Sharpe**: > 1.5
✅ **Max Drawdown**: < 8%
✅ **Annual Projection**: £10k → £50-100k+

✅ **Operational**: 22-hour continuous, 20k tickers, 33 modules, Ouroboros learning, telemetry live
✅ **Safety**: Kill switch (< 100ms), circuit breaker (2% halt), 100% audit trail, PnL accurate to pence
✅ **Production**: £10k deployed, 7+ days of profitable live trading

---

## GETTING STARTED NOW

1. **Read the full master plan**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/COMPLETE_MASTER_PLAN_1000H.md`
2. **Execute Phases 3-6 today**: 4.5 hours
3. **Deploy to EC2**: `bash scripts/deploy_to_ec2.sh`
4. **Verify**: `cargo test --release && cargo run --release`
5. **Monitor**: Tail logs in real-time

**You have everything you need. Go execute.**
