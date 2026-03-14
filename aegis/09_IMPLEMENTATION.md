# AEGIS — Implementation Phases + Parameters
> Delivery timeline, go-live gate, parameter tables.
> Extracted from AEGIS Master Plan v16.2.
> See [README](README.md) for full index.
---

# SECTION 9: IMPLEMENTATION PHASES {#section-9}

## Phase 0A: EXECUTION TIMING FIXES (Week 1) — DO THIS FIRST

**The system has 0% win rate. Nothing else matters until timing is fixed.**

**The 11-Fix Timing + Microstructure Sprint** (~36.5 hours):

| # | Fix | File | Time | Impact |
|---|-----|------|------|--------|
| 1 | T-08: Remove `_daily_signal_fired` | `daily_target.py:348,497` | 0.5h | Unblocks multi-trade |
| 2 | T-01: Replace 30-min blackout with observe-then-act | `daily_target.py:324-333` | 3h | Opens highest-alpha window |
| 3 | T-02: Replace lunch blackout with reduced-confidence | `daily_target.py:335-344` | 2h | Opens US pre-market repricing |
| 4 | T-06: Lower ADX to 15/20 (was 25) | `daily_target.py:77-79` | 1h | Catches trend starts |
| 5 | T-04: Move GPD to nightly batch + Redis | `daily_target.py:414-435` | 4h | Removes 24s scan latency |
| 6 | T-05: FAST/SLOW tier indicator reweight | `daily_target.py:127-202` | 6h | Fires on gap moves immediately |
| 7 | T-03: Event-driven 60s heartbeat scanning | `main.py` scheduler | 8h | Detects moves within 60s |
| 8 | T-10: FAST path qualification (7 gates) | `main.py:1823-2850` | 6h | Signal-to-order < 500ms |
| 9 | RO-01: Toxic spread hard-cap 35 bps (09:00-09:10) | `daily_target.py`, `cost_model.py` | 2h | Protects T-01 gap window from spread bleed |
| 10 | RO-04: Gap scan sizing penalty (0.50x Kelly) | `dynamic_sizer.py` | 2h | Reduces tail risk in gap window |
| 11 | SA-01: SLOW indicator background pre-computation | `main.py` (new thread) | 2h | Decouples FAST/SLOW from T-05 |

Validation: 48h paper trading window. Compare: signals generated, entry timing vs daily high, stop-out rate. Must show improvement over 0% baseline.

## Phase 0B: Critical Bug Fixes (Week 2)

**The 14-Fix Risk + Microstructure Sprint** (~25 hours):

| # | Fix | File | Time |
|---|-----|------|------|
| 1 | SessionProtection 0.015->0.025 | `risk_sizer.py:370` | 30s |
| 2 | Signal list mutation (list comprehension) | `main.py:1929` | 5min |
| 3 | ML should_retrain() fix | `ml_meta_model.py:537` | 5min |
| 4 | asyncio.QueueFull -> queue.Full | `main.py:3081,4208,4437` | 15min |
| 5 | ISA .L correlation families | `dynamic_sizer.py:1302` | 30min |
| 6 | VIX proportional deadband (5%) | `regime_classifier.py` | 1h |
| 7 | Wire decrement_transition_buffer() | `regime_classifier.py:293` | 30min |
| 8 | Align _REGIME_MAP with RegimeState | `ml_meta_model.py:48` | 30min |
| 9 | SHOCK_RECOVERY by date not per-call | `dynamic_sizer.py:528` | 30min |
| 10 | __setattr__ guard on ImmutableRiskRules | `risk_sizer.py:30-59` | 30min |
| 11 | RO-02: 3x instant-stopout circuit breaker = halt session | `circuit_breakers.py`, `virtual_trader.py` | 2h |
| 12 | RO-03: Underlying inventory limit (max 1 per underlying) | `portfolio_risk.py`, `isa_universe.py` | 2h |
| 13 | AR-03: Walk-forward validation with purge/embargo for ML | `ml_meta_model.py`, `learning_engine.py` | 6h |
| 14 | AR-04: Regime-conditioned Go-Live gates (40% WR per regime) | `sprint6_live_gate.py`, `go_nogo.py` | 4h |

Then: VIX fail-closed, Elastic IP, S3 backup cron.
Validation: 24h paper trading window.

## Phase 1: Execution + ISA Gate (Weeks 2-3)

- ISA Three-Key Safe gate (P0-CRITICAL)
- Signal queue consumer implementation
- Profit ladder consolidation (3 -> 1 canonical)
- Bayesian stranger penalty
- Inverse pivot for bearish regimes
- V2 multi-signal S15 upgrade (remove MAX_SIGNALS_PER_DAY=1)

## Phase 2: Universe Expansion (Weeks 4-6)

- Amihud Capacity Sieve module
- ASER filter in LSE registry
- DSR graduation gate
- Apex Scout module
- Full 60+ ETP CORE tier activation

## Phase 3: Intelligence & Ops (Weeks 7-8)

- Walk-forward ML validation
- Tiered Telegram alerts
- Weekly performance report
- CloudWatch monitoring

## Phase 4: Scale Preparation (Weeks 9-12)

- AUM-scaled parameters
- TWAP/VWAP execution
- PostgreSQL migration
- CI/CD pipeline

## Go-Live Gate (After Phase 2 + 63 MTRL Days)

| Criterion | Threshold |
|-----------|-----------|
| DSR | >= 3.0 (HLZ 2016) |
| Win Rate (S15) | >= 50% on 60+ trades |
| Max Drawdown | < 6% during paper |
| System Uptime | > 99.5% over 30 days |
| P0 Fixes | All verified |
| CDaR_95 | Never > 5% |
| Paper Duration | 63 MTRL days minimum |
| Dropped P0 Signals | 0 |
| ISA Compliance | 100% (0 non-ISA trades) |
| False Flatten Events | 0 |
| Market Data Feed | Real-time API (NOT yfinance) — **SATISFIED: IBKR IB Gateway via ibkr_source.py** |
| **S15 Win Rate (post-timing fix)** | **>= 40% on 30+ trades (was 0% on 52 trades)** |
| **Entry Timing Score** | **Median < 0.50 across 100 trades (entering in first half of move, not tail end). HARD GATE (Gemini Q10): must pass alongside RK-01 WR >= 40%. Both gates required.** |
| **Signal-to-Order Latency** | **FAST path < 500ms, SLOW path < 3s** |
| **Gate Rejection False Positive Rate** | **< 30% of rejected signals profitable** |
| SQLite Write Queue | Async writer active, 0 lock errors in 30 days |
| Regime Coverage | Paper period includes >= 5 days HIGH_VOL + >= 2 days RISK_OFF (real or simulated stress test) |
| Stamp Duty Verification | All CORE ETPs verified exempt. stamp_duty_exempt=true in TICKER_REGISTRY. |
| Operator Override Rate | Signal rejection rate during Limited Live < 20% |
| Alpha Decay Monitor | Monthly rolling Sharpe of S15 signals tracked. No decline > 0.3 over 6 months. |

---

# SECTION 9B: LIMITED LIVE TRANSITION {#section-9b}

| Parameter | Limited Live | Expanded Limited | Full Live |
|-----------|-------------|-----------------|-----------|
| Max capital | 1,000 | 3,000 | 10,000 |
| Max positions | 1 | 2 | 4 |
| Strategy | S15 only | S15 only | All |
| Order type | LIMIT only | LIMIT only | Market (10K), Limit (50K+) |
| Human confirmation | Yes (every trade) | Yes (every trade) | Fully automated |
| Duration | Min 2 weeks (10 MTRL days) | Min 2 weeks (10 MTRL days) | Ongoing |

**Expanded Limited Live** tests multi-position mechanics (correlation brake, portfolio heat, concurrent exits, SQLite write queue) without full capital exposure. The 1-position to 4-position jump was identified as a phase-transition risk — bugs that only manifest with concurrent positions (e.g., DB locking, signal iteration mutation) would not be caught in single-position Limited Live.

**Human Confirmation During Limited Live**: Log ALL signals — confirmed and rejected. Track hypothetical P&L of rejected signals. If rejected signals would have been profitable >50% of the time, the human is degrading performance — flag as Go-Live Gate metric.

---

# SECTION 10: PARAMETER TABLES {#section-10}

## Table A: Immediate Changes (Phase 0A — Timing Fixes)

| Parameter | Old | New | Rationale |
|-----------|-----|-----|-----------|
| Opening blackout | 30-min hard veto | 5-min observe + gap scan | S15 0% WR from late entry |
| Lunch blackout | 90-min hard veto | -10 confidence (not veto) | Blocks US pre-market repricing |
| `_daily_signal_fired` | 1 signal/day | REMOVED | Old V1 code, plan says remove |
| ADX threshold | 25 (confirmed trend) | 15 FAST / 20 SLOW | Catches trend starts not confirmations |
| MIN_RVOL | 0.85 | 0.30 FAST / 0.65 SLOW | Gap moves start on low volume |
| RVOL late-day trough | 1.5 | 0.80 | 1.5 = 95th percentile, unreasonable |
| Indicator consensus | 6/8 flat weight | FAST 3/4 leading + SLOW adds confidence | Lagging indicators can't confirm gaps |
| GPD tail risk | Inline (24s/cycle) | Nightly batch + Redis read (<1ms) | Massive scan latency reduction |
| Scan mode | Fixed cron schedule | 60s heartbeat + event trigger | Detect moves within 60s, not 2h |
| Qualification path | 18 gates, 4.5s | 7-gate FAST path, <500ms | Late execution kills trades |

## Table A2: Phase 0B Changes (Risk Fixes)

| Parameter | Old | New | Rationale |
|-----------|-----|-----|-----------|
| Signal queue | Bounded FIFO (50) | Unbounded priority | Prevent signal loss |
| Regime confirmation | 0 ticks | 3 ticks | Prevent whipsaw |
| VIX default | 0 (fail-open) | 99 (fail-closed) | Prevent false aggression |
| VIX cache TTL | 30 min | 5 min | Stale VIX in crises |
| Lunch RVOL | 1.7 | 1.3 | Filters 95% of setups |
| ML confidence feature | Included | REMOVED | Feature leakage |
| Inverse ETP list | Hardcoded | Metadata query | Dynamic |
| VIX hysteresis | 0 | 5% proportional | Prevent 60s regime flip |

## Table B: Sacred Parameters (NEVER change without Amendment Procedure)

| Parameter | Value | Source |
|-----------|-------|--------|
| Risk per trade | 0.75% | Kelly (1956) |
| ATR stop multiplier | 1.5x | Le Beau (1999) |
| Power Hour boost | +15% | Heston et al. (2010) |
| SHAP drift threshold | >5 positions | Lundberg & Lee (2017) |
| CUSUM threshold | 3.0 sigma | Page (1954) |
| HMM latent states | 3 | Hamilton (1989) |
| Profit ladder | 6 rungs (VT inline) | Empirically validated |
| Daily loss cascade | L1/L2/L3 | Risk Constitution |
| Emergency flatten | -5% portfolio / -15% position | Calibrated for 3x ETPs |

---
