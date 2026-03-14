# AEGIS V2 COMPLETE WIRING SPECIFICATION
## All 32 Phases, Every Connection, Every Wire Explicitly Defined

**Date**: March 13, 2026
**Purpose**: Eliminate all ambiguity; show every data flow, every dependency, every handoff
**Status**: Ready for implementation team approval
**Total Phases**: 32 (Phases 1-25 base + Phases 26-29 DQN + Phases 30-32 global)

---

## I. MASTER WIRING DIAGRAM (TEXT FORM)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DATA FEEDS (Input Layer)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ IBKR Primary (LSE/NYSE/EUR/ASX, <100ms)  → Route by Market                 │
│ yfinance Secondary (15-min delayed)      → Route by Market                 │
│ Polygon Tertiary (1-min, real-time)      → Route by Market                 │
│ Redis Cache (last seen value)            → Route by Market                 │
│                                                                              │
│ ALL FLOWS TO:                                                               │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ PHASE 0: FEED MANAGER (Rust: src/feed_manager.rs)                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Input:  Ticks from IBKR/yfinance/Polygon/Redis                            │
│ Logic:  Select best available (IBKR>yfinance>Polygon>Redis)               │
│         Check staleness (<2min threshold)                                   │
│         Detect market (LSE vs NYSE vs EURONEXT vs ASX vs TSE)             │
│ Output: TickContext { market, symbol, price, bid, ask, volume, timestamp }│
│                                                                              │
│ WIRES OUT TO:                                                               │
│   → Phase 5 (Regime Detection)                                             │
│   → Phase 26 (DQN Feature Extraction, if enabled)                         │
│   → Phase 30 (FX Manager, if non-LSE)                                     │
│   → Phase 31 (Geopolitical Risk, if non-LSE)                              │
│                                                                              │
│ FILES:                                                                       │
│   - rust_core/src/feed_manager.rs (NEW, 200 lines)                        │
│   - rust_core/src/types.rs (add TickContext struct)                       │
│                                                                              │
│ TESTS:                                                                       │
│   - [TEST] IBKR ticker arrives → routed to Phase 5                         │
│   - [TEST] IBKR stale >2min → failover to yfinance                         │
│   - [TEST] Euronext ticker → detected, labeled for FX adjustment           │
│   - [TEST] ASX ticker → detected, labeled for overnight mode               │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                       │
          ┌────────────────────────────┼──────────────────────────────┐
          │                            │                              │
          ▼                            ▼                              ▼
┌───────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
│ PHASE 5: REGIME   │    │ PHASE 26: DQN        │    │ PHASE 30: FX        │
│ DETECTION         │    │ FEATURE EXTRACTION   │    │ MANAGER             │
│ (Rust:clock.rs)   │    │ (Python+Rust bridge) │    │ (Rust:fx_manager.rs)│
│                   │    │                      │    │                     │
│ Input: TickContext│    │ Input: TickContext   │    │ Input: TickContext  │
│        VIX, Vol   │    │        (5s/30s/60s)  │    │        (EUR/AUD/JPY) │
│                   │    │        OHLCV         │    │                     │
│ Output: Regime    │    │        Microstructure│    │ Output: FX cost,    │
│  {TRENDING_UP,    │    │        Volatility    │    │   Adjusted size     │
│   RANGE,          │    │        Time-of-day   │    │                     │
│   HIGH_VOL,       │    │        Sector mom    │    │ Wires out to:       │
│   RISK_OFF}       │    │                      │    │   → Phase 9         │
│                   │    │ Output: Features     │    │   (Position Sizer)  │
│ Wires out to:     │    │  [batch,20,32]      │    │   → Phase 15        │
│   → Phase 6       │    │  (Tensor)            │    │   (Order Router)    │
│   → Phase 7       │    │                      │    │   → Phase 19        │
│   → Phase 9       │    │ Wires out to:        │    │   (Risk Mgr)        │
│   → Phase 22      │    │   → Phase 27 (DQN)   │    │                     │
│   → Phase 27 (DQN)│    │   → Phase 29 (Live)  │    │ FILES:              │
│                   │    │                      │    │  fx_manager.rs(150) │
│ FILES:            │    │ FILES:               │    │  types.rs (extend)  │
│  clock.rs (90+)   │    │  extract_candles.py  │    │                     │
│  ext/hh.rs (HMM)  │    │  extract_orderbook   │    │ TESTS:              │
│                   │    │  extract_volatility  │    │  [TEST] EUR exposure│
│ TESTS:            │    │  extract_context.py  │    │  calc FX cost daily │
│ [TEST] VIX<15 →   │    │  dataset.py (NEW)    │    │  [TEST] Position    │
│   TRENDING_UP     │    │                      │    │  adjusted for FX    │
│ [TEST] Vol spike  │    │ TESTS:               │    │  [TEST] AUD hedge   │
│   → HIGH_VOL      │    │ [TEST] Feature       │    │  via forward        │
│ [TEST] Credit     │    │   pipeline works     │    │  [TEST] ASX AUD cost│
│   spread >200 →   │    │ [TEST] Dataset       │    │  reduces position   │
│   RISK_OFF        │    │   balanced classes   │    │  size properly      │
└─────┬─────────────┘    └──────────┬───────────┘    └─────┬───────────────┘
      │                             │                      │
      └─────────────────────────────┼──────────────────────┘
                                    │
                   ┌────────────────┼────────────────┐
                   │                │                │
                   ▼                ▼                ▼
      ┌──────────────────────┐    ┌─────────────────────────┐
      │ PHASE 6: VOLATILITY  │    │ PHASE 31: GEOPOLITICAL │
      │ SCALER               │    │ RISK MANAGER            │
      │ (Rust:vol_scaler.rs) │    │ (Rust:geopolitical.rs)  │
      │                      │    │                         │
      │ Input: Vol, Regime   │    │ Input: TickContext      │
      │        (from Phase 5)│    │        (news API scan)   │
      │                      │    │                         │
      │ Logic: vol_scalar =  │    │ Logic: Check news API   │
      │  1.0/(vol/15%)       │    │  for keywords per market │
      │  cap [0.5,1.5x]      │    │ Update risk level:      │
      │  HIGH_VOL → 1.0x     │    │  LOW (1.0x) / MEDIUM    │
      │  RISK_OFF → 0.5x     │    │  (0.7x) / HIGH (0.3x) / │
      │                      │    │  HALT (0.0x)            │
      │ Output: vol_scalar   │    │                         │
      │                      │    │ Output: Position        │
      │ Wires to:            │    │  multiplier per market  │
      │   → Phase 9          │    │                         │
      │   (Position Sizer)   │    │ Wires to:               │
      │                      │    │   → Phase 15            │
      │ FILES:               │    │   (Order Router)        │
      │  vol_scaler.rs (80)  │    │                         │
      │                      │    │ FILES:                  │
      │ TESTS:               │    │  geopolitical.rs (200)  │
      │ [TEST] Vol=10% →     │    │  newsapi integration    │
      │  scalar=1.5x         │    │                         │
      │ [TEST] Vol=30% →     │    │ TESTS:                  │
      │  scalar=0.5x capped  │    │ [TEST] "sanctions" →    │
      │ [TEST] HIGH_VOL reg  │    │  HIGH risk (0.3x)       │
      │  → scalar capped 1.0x│    │ [TEST] Risk escalation  │
      └──────┬───────────────┘    │  auto-triggers          │
             │                    │ [TEST] >3 halts/hr →    │
             │                    │  reduce positions 50%   │
             │                    └────────┬────────────────┘
             │                             │
             └─────────────────┬───────────┘
                               │
                               ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 7: CONFIDENCE SCORER                 │
      │ (Python:brain/strategies/vanguard_sniper.py)│
      │                                             │
      │ Input: TickContext (from Phase 0)          │
      │        Regime (from Phase 5)               │
      │        Vol_scalar (from Phase 6)           │
      │        DQN signal (from Phase 29, if live) │
      │                                             │
      │ Logic: 8-indicator weighted score:         │
      │  1. VWAP momentum (1.8x)                   │
      │  2. RSI (1.2x)                             │
      │  3. EMA (0.8x)                             │
      │  4. ROC (1.0x)                             │
      │  5. MACD (1.0x)                            │
      │  6. ADX (1.5x)                             │
      │  7. Bollinger Bands (0.7x)                 │
      │  8. Volume (0.9x)                          │
      │                                             │
      │  Combined confidence = weighted_sum        │
      │                                             │
      │  IF DQN_LIVE:                              │
      │    confidence = blend(8-ind, DQN)          │
      │                                             │
      │ Output: confidence_score [0-100]           │
      │         scores_dict (per-indicator)        │
      │                                             │
      │ Wires to:                                  │
      │   → Phase 4 (White Reality Check)          │
      │   → Phase 8 (Pre-Conditions Gate)          │
      │   → Phase 9 (Position Sizer)               │
      │                                             │
      │ FILES:                                      │
      │  vanguard_sniper.py (150+)                 │
      │  dqn_transformer_v1.py (400, if live)      │
      │  apex_scout.py (100+)                      │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] All 8 indicators calculated         │
      │ [TEST] Weighted sum in [0,100]             │
      │ [TEST] Confidence ≥6.5 → proceed           │
      │ [TEST] Confidence <6.5 → skip trade        │
      │ [TEST] DQN enabled → blends signals        │
      │ [TEST] DQN fallback works                  │
      └────────┬──────────────────────────────────┘
               │
               ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 4: WHITE REALITY CHECK               │
      │ (Rust:risk_arbiter.rs, CHECK 4)            │
      │                                             │
      │ Input: confidence (from Phase 7)           │
      │        Signal history (50+ obs per regime) │
      │                                             │
      │ Logic: Compute Deflated Sharpe Ratio       │
      │  DSR = (Sharpe - SR_null) / sqrt(var)      │
      │  Adjusted for multiple comparisons         │
      │  Require DSR > 1.0 (world-class signal)    │
      │  Bootstrap confidence interval (Efron 79)  │
      │  Per-regime testing (all 5 regimes)        │
      │                                             │
      │ Gate: IF DSR < 1.0 → FAIL (don't trade)   │
      │       IF DSR ≥ 1.0 → PASS (proceed)       │
      │                                             │
      │ Output: is_significant (bool)              │
      │         dsr_score (float)                  │
      │         p_value (confidence)               │
      │                                             │
      │ Wires to:                                  │
      │   → Phase 8 (Pre-Conditions Gate)          │
      │   → Phase 15 (Order Router)                │
      │                                             │
      │ FILES:                                      │
      │  risk_arbiter.rs (150+, extend CHECK 4)    │
      │  dsr_calculator.rs (NEW, 100 lines)        │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] DSR > 1.0 → PASS                    │
      │ [TEST] DSR < 1.0 → FAIL                    │
      │ [TEST] Bootstrap CI computed               │
      │ [TEST] Per-regime DSR ≥ 1.0 all regimes    │
      │ [TEST] Signal disabled 1 week if DSR<0.5   │
      └────────┬──────────────────────────────────┘
               │
               ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 8: PRE-CONDITIONS GATE               │
      │ (Rust:risk_arbiter.rs, CHECK 5)            │
      │                                             │
      │ Input: Account state                       │
      │        Market status                       │
      │        Order queue length                  │
      │        From Phase 4 (White Reality)        │
      │                                             │
      │ Logic: Binary checks (all must PASS):      │
      │  1. ISA account ACTIVE? (CHECK Phase 2)    │
      │  2. Margin debt = £0? (CHECK Phase 2)      │
      │  3. Available cash sufficient? (Phase 1)   │
      │  4. Circuit breaker = GREEN? (Phase 19)    │
      │  5. Order queue < 50? (no overload)        │
      │  6. White Reality Check PASS? (Phase 4)    │
      │  7. Confidence ≥ threshold? (Phase 7)      │
      │                                             │
      │ Output: PASS (all checks ok) or            │
      │         FAIL (any check fails)             │
      │                                             │
      │ If FAIL: Queue order, retry (max 10x)      │
      │ If PASS: Proceed to Phase 9                │
      │                                             │
      │ Wires to:                                  │
      │   → Phase 9 (Position Sizer)               │
      │   → Phase 15 (Order Router)                │
      │                                             │
      │ FILES:                                      │
      │  risk_arbiter.rs (extend CHECK 5)          │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] All 7 checks execute                │
      │ [TEST] Any fail → FAIL gate                │
      │ [TEST] Queue retry logic works             │
      │ [TEST] Max 10 retries enforced             │
      └────────┬──────────────────────────────────┘
               │
               ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 9: POSITION SIZER                    │
      │ (Python:brain/sizing/kelly_12factor.py)    │
      │                                             │
      │ Input: kelly_max (from Phase 1)            │
      │        regime (from Phase 5)               │
      │        vol_scalar (from Phase 6)           │
      │        confidence (from Phase 7)           │
      │        fx_adjustment (from Phase 30, ≥1)   │
      │        geo_multiplier (from Phase 31, ≥1)  │
      │        underlying_symbol                   │
      │                                             │
      │ Logic: Kelly 12-factor sizing:             │
      │  1. Base Kelly from win rate (shrinkage)   │
      │  2. Volatility decay (3x: ÷9, 5x: ÷25)   │
      │  3. Moreira-Muir volatility scaling        │
      │  4. Correlation penalty                    │
      │  5. Drawdown scaling                       │
      │  6. Regime scaling (normal/reduce/flatten) │
      │  7. Spread cost adjustment                 │
      │  8. Time-of-day scaling                    │
      │  9. Confidence scaling [0.65-1.0]          │
      │  10. Leverage position cap (0.2 max)       │
      │  11. FX adjustment (multiply by fx_adj)    │
      │  12. Geopolitical multiplier (multiply)    │
      │                                             │
      │  leverage_prioritization:                  │
      │  IF underlying in MAP AND LSE_OPEN:        │
      │    symbol = get_5x_etp(underlying)         │
      │    IF confidence ≥ 7.0:                    │
      │      size *= 1.5 (bonus for high conf)     │
      │  ELIF underlying in MAP AND LSE_OPEN:      │
      │    symbol = get_3x_etp(underlying)         │
      │  ELSE:                                      │
      │    symbol = underlying (1x)                │
      │                                             │
      │  position_size_final = min(               │
      │    position_size,                         │
      │    max_daily_heat_remaining               │
      │  )                                         │
      │                                             │
      │ Output: position_size (shares)             │
      │         symbol (ticker w/ leverage)        │
      │         reason (for logging)               │
      │                                             │
      │ Wires to:                                  │
      │   → Phase 10 (Execution Quality)           │
      │   → Phase 15 (Order Router)                │
      │   → Phase 19 (Risk Manager)                │
      │                                             │
      │ FILES:                                      │
      │  kelly_12factor.py (192+)                  │
      │  types.py (Position struct)                │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] Kelly formula correct               │
      │ [TEST] All 12 factors apply                │
      │ [TEST] FX adjustment reduces size          │
      │ [TEST] Geo multiplier applies              │
      │ [TEST] Leverage prioritization works       │
      │  (NVDA → NVD3.L, QQQ → QQQ3.L/QQQS.L)    │
      │ [TEST] Position capped at heat remaining   │
      │ [TEST] Fractional shares via floor()       │
      └────────┬──────────────────────────────────┘
               │
               ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 10: EXECUTION QUALITY                │
      │ (Rust:execution_quality.rs)                │
      │                                             │
      │ Input: symbol, position_size (Phase 9)     │
      │        bid, ask, volume (Phase 0)          │
      │        regime (Phase 5)                    │
      │                                             │
      │ Logic: Estimate slippage + optimal timing  │
      │  Expected slippage:                        │
      │   LSE: 10-30 bps                           │
      │   US: 8-20 bps                             │
      │   Euronext: 15-40 bps                      │
      │   ASX: 20-100 bps                          │
      │                                             │
      │  Optimal entry windows:                    │
      │   Phase 1 (LSE): Pre-bell 08:00-08:15     │
      │   Phase 2 (Hybrid): US open 14:30-14:45   │
      │   Phase 3 (US): Mid-afternoon 17:00-19:00 │
      │   Phase 4 (Asia): Open 23:50-00:30        │
      │                                             │
      │  Participation rate: 20-30% of volume      │
      │                                             │
      │ Output: expected_fill_price                │
      │         entry_timing_score [0-1.0]         │
      │                                             │
      │ Wires to:                                  │
      │   → Phase 15 (Order Router)                │
      │                                             │
      │ FILES:                                      │
      │  execution_quality.rs (NEW, 120 lines)     │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] Slippage modeled per market         │
      │ [TEST] Entry timing score calculated       │
      │ [TEST] Participation rate checked          │
      └────────┬──────────────────────────────────┘
               │
        ┌──────┴──────┬──────────────┬──────────────┐
        │             │              │              │
        ▼             ▼              ▼              ▼
    ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐
    │ PHASE 2 │  │ PHASE 3  │  │ PHASE 15 │  │ PHASE 19    │
    │ ISA AUD │  │COMPLIANCE│  │ ORDER    │  │ RISK MANAGER│
    │ (Every  │  │ GATES    │  │ ROUTER   │  │ (Stops, Heat│
    │  5 min) │  │(Pre-ord) │  │(Submit)  │  │  Cap, CB)   │
    │         │  │          │  │          │  │             │
    │Verify:  │  │CHECK:    │  │INPUT:    │  │INPUT:       │
    │ Margin=0│  │ Margin   │  │ symbol   │  │ Position    │
    │ ISA ok  │  │ Spread   │  │ size     │  │ Entry price │
    │ No halt │  │ Trading  │  │ regime   │  │ Regime      │
    │         │  │ halts    │  │          │  │ Current pr  │
    │Output:  │  │          │  │OUTPUT:   │  │ Daily P&L   │
    │PASS/FAIL│  │PASS/FAIL │  │order_id  │  │             │
    │         │  │          │  │ fill_pr  │  │LOGIC:       │
    │Wires:   │  │Wires:    │  │ slippage │  │ Stop loss:  │
    │ → Ph 15 │  │ → Ph 15  │  │          │  │  TREND_UP 3%│
    │ (block) │  │ (block)  │  │Wires:    │  │  RANGE 1.5% │
    │ → Ph 20 │  │          │  │ → Ph 20  │  │  HIGH_VOL 2%│
    │ (audit) │  │          │  │ (log)    │  │  RISK_OFF 1%│
    │         │  │          │  │ → Ph 21  │  │             │
    │FILES:   │  │FILES:    │  │ (reconcil)  │ Portfolio h: │
    │isa_gate │  │risk_arb  │  │          │  │  L1: -1.5%  │
    │   .rs   │  │   .rs    │  │FILES:    │  │  L2: -2.5%  │
    │(150)    │  │(150)     │  │smart_rout│  │  L3: -4.0%  │
    │         │  │          │  │   .rs(15 │  │ (flatten)   │
    │TESTS:   │  │TESTS:    │  │0)        │  │             │
    │ Marg=£0 │  │ Spread   │  │          │  │WIRES:       │
    │ → PASS  │  │ OK → PAS │  │TESTS:    │  │ → Ph 21     │
    │ Marg>£0 │  │ Spread   │  │ Order    │  │ (close)     │
    │ → FAIL  │  │ bad→FAIL │  │ submitted│  │ → Ph 20     │
    │ HALT↔5m │  │ Trading  │  │ to IBKR  │  │ (reconcile) │
    │ → HALT  │  │ halt→FIL │  │ Fill pr  │  │ → Monitor   │
    │ Re-audit│  │          │  │ logged   │  │   daily     │
    │ 5 min   │  │          │  │ Slippage │  │             │
    │         │  │          │  │ tracked  │  │TESTS:       │
    └─────────┘  │          │  │          │  │ Stops set   │
                 │          │  │          │  │ Heat tracked│
                 │          │  │          │  │ L1/L2/L3    │
                 │          │  │          │  │ escalation  │
                 │          │  │          │  │ works       │
                 │          │  │          │  │ Auto-reduce │
                 │          │  │          │  │ on heat>2%  │
                 │          │  │          │  │ Flatten on  │
                 │          │  │          │  │ L3 -4.0%    │
                 └──────────┴──┴──────────┴──┴─────────────┘
                            │
                            ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 21: POSITION MANAGEMENT              │
      │ (Rust:position_manager.rs)                 │
      │                                             │
      │ Input: Closed positions (Phase 19/20)      │
      │        Current portfolio                   │
      │        Market data                         │
      │                                             │
      │ Logic: Track all positions                 │
      │        Update P&L continuously             │
      │        Close at targets/stops/time-based    │
      │        Rebalance portfolio                 │
      │                                             │
      │ Output: Updated portfolio state            │
      │         Closed positions (for attr.)        │
      │                                             │
      │ Wires to:                                  │
      │   → Phase 20 (Reconciliation)              │
      │   → Phase 23 (Performance Attribution)     │
      │   → Database (logging)                     │
      │                                             │
      │ FILES:                                      │
      │  position_manager.rs (120)                 │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] Positions tracked correctly         │
      │ [TEST] P&L updated in real-time            │
      │ [TEST] Closed positions logged             │
      └────────┬──────────────────────────────────┘
               │
               ▼
      ┌────────────────────────────────────────────┐
      │ PHASE 20: RECONCILIATION AUDITOR           │
      │ (Rust:isa_gate.rs, every 5 min)           │
      │                                             │
      │ Input: Account holdings (from Phase 21)    │
      │        Margin debt                         │
      │        Cash balance                        │
      │                                             │
      │ Logic: ISA compliance audit (every 5 min): │
      │  1. Margin debt = £0? (MUST be zero)       │
      │  2. All holdings ISA-eligible? (whitelist) │
      │  3. No naked shorts? (inverse ETPs ok)     │
      │  4. No margin trading? (100% paid)         │
      │                                             │
      │ Output: is_compliant (bool)                │
      │         violations (list if any)           │
      │                                             │
      │ If COMPLIANT: Continue                     │
      │ If NOT: HALT all trading + alert           │
      │         (Escalation required)              │
      │                                             │
      │ Wires to:                                  │
      │   → Alert system (if violations)           │
      │   → Phase 15 (blocks new orders if FAIL)   │
      │   → Database (audit trail)                 │
      │                                             │
      │ FILES:                                      │
      │  isa_gate.rs (extend CHECK 19)             │
      │                                             │
      │ TESTS:                                      │
      │ [TEST] Margin debt = £0 → PASS             │
      │ [TEST] Margin debt > £0 → FAIL + HALT      │
      │ [TEST] Non-ISA holding → FAIL + alert      │
      │ [TEST] Naked short → FAIL + alert          │
      │ [TEST] Every 5 min execution verified      │
      └─────────────────────────────────────────────┘
                            │
                            │
         ┌──────────────────┴──────────────────┐
         │                                     │
         ▼                                     ▼
      ┌──────────────────────────┐  ┌──────────────────────────┐
      │ PHASE 22-24: OUROBOROS   │  │ DATABASE / LOGGING       │
      │ NIGHTLY (22:00-23:50)    │  │ (All phases log to DB)   │
      │                          │  │                          │
      │ Phase 23: Performance    │  │ Logs:                    │
      │ Attribution              │  │  - All trades executed   │
      │  (10 min)                │  │  - P&L attribution       │
      │ → Decompose returns      │  │  - Signal metrics        │
      │ → Win rate per regime    │  │  - Risk checks           │
      │                          │  │  - ISA audit trail       │
      │ Phase 22: DQN Weighting  │  │  - Alert history         │
      │  (10 min)                │  │  - Model parameters      │
      │ → Retrain indicators     │  │  - Geopolitical risk     │
      │ → Update weights         │  │  - FX costs              │
      │                          │  │  - Execution metrics     │
      │ Phase 24: ML Adaptation  │  │                          │
      │  (10 min)                │  │ Used by:                 │
      │ → Update thresholds      │  │  - Phase 23 (attr)       │
      │ → Adjust leverage        │  │  - Phase 24 (adapt)      │
      │                          │  │  - Universe scan         │
      │ Phase 25: Orchestrator   │  │  - Monitoring dashboard  │
      │  (2 min)                 │  │  - Daily reports         │
      │ → Commit changes         │  │                          │
      │ → Ready for 08:00 LSE    │  │ FILES:                   │
      │                          │  │  - PostgreSQL database   │
      │ Wires to:                │  │  - Table: trades         │
      │  → Phase 5/7/9 (use new) │  │  - Table: risk_events    │
      │    params next day)       │  │  - Table: model_params   │
      │  → Universe scan         │  │  - Table: geopolitical   │
      │                          │  │  - Schema: monitoring.sql│
      │                          │  │                          │
      │ FILES:                   │  │ TESTS:                   │
      │  ouroboros_attribution   │  │ [TEST] Trades logged     │
      │   .py (new)              │  │ [TEST] Daily reports gen │
      │  dqn_retraining.py (new) │  │ [TEST] All data intact   │
      │  threshold_update.py(new)│  │ [TEST] Audit trail audit │
      │  orchestrator.py (new)   │  │                          │
      │                          │  │                          │
      │ TESTS:                   │  │                          │
      │ [TEST] P&L attribution   │  │                          │
      │  correct                 │  │                          │
      │ [TEST] Win rate updated  │  │                          │
      │ [TEST] DQN retrained     │  │                          │
      │ [TEST] New thresholds    │  │                          │
      │  applied next day        │  │                          │
      │ [TEST] Orchestrator      │  │                          │
      │  commits all changes     │  │                          │
      │ [TEST] System ready for  │  │                          │
      │  08:00 LSE open          │  │                          │
      └──────────────────────────┘  └──────────────────────────┘
```

---

## II. PHASE DEPENDENCY MATRIX (All Wires Explicit)

### STARTUP SEQUENCE (Week 0, Day 1)

```
INIT: Read config files
  → config/contracts.toml (1,770 assets)
  → config/config.toml (90+ parameters)
  → config/initial_universe.toml (bootstrap)

LOAD (in order):
  1. Phase 1: Capital Preservation
     - Load Kelly parameters
     - Load historical returns (252 epochs)
     - Initialize heat tracking

  2. Phase 2: ISA Auditor
     - Load ISA whitelist (12 core + 80 global)
     - Load FCA/HMRC ruleset
     - Initialize 5-minute audit timer

  3. Phase 30: FX Manager
     - Load FX rates (EUR/GBP, AUD/GBP, JPY/GBP)
     - Load hedging costs (15/25/30 bps per month)
     - Initialize per-currency exposure tracker

  4. Phase 31: Geopolitical Risk Manager
     - Load NewsAPI credentials
     - Load geopolitical risk baseline (LOW for Euronext/ASX)
     - Initialize news monitoring

  5. Phase 0: Feed Manager
     - Connect to IBKR (port 4004, client_id=101)
     - Connect to yfinance backup
     - Connect to Polygon.io backup
     - Initialize Redis cache

  6. Phase 5: Regime Detection
     - Load HMM model (5 states)
     - Load VIX + realized vol formulas
     - Initialize regime tracker

  7. Phase 6-7: Volatility Scaler + Confidence Scorer
     - Load 8-indicator formulas
     - Load weights per indicator
     - Initialize scoring engine

  8. Phase 26 (if DQN enabled): DQN Models
     - Load trained Transformer encoder
     - Load 5 regime-specific DQN models
     - Initialize feature extraction pipeline

  9. Database:
     - Connect to PostgreSQL
     - Create tables (trades, risk_events, model_params, geopolitical)
     - Initialize audit log

READY: System online, listening for ticks
```

### INTRADAY LOOP (Every 100ms per tick)

```
TICK RECEIVED from IBKR/yfinance/Polygon
  │
  ├─→ Phase 0: Feed Manager
  │    Route by market (LSE/NYSE/EURONEXT/ASX/TSE)
  │    Check staleness (<2min)
  │    Output: TickContext {market, symbol, bid, ask, vol, price}
  │
  ├─→ Phase 5: Regime Detection
  │    Input: VIX, realized_vol (from market data)
  │    Classify regime (TRENDING_UP/RANGE/RISK_OFF/etc)
  │    Output: current_regime
  │
  ├─→ Phase 6: Volatility Scaler
  │    Input: realized_vol, regime
  │    Calculate: vol_scalar [0.5, 1.5x]
  │    Output: vol_scalar
  │
  ├─→ Phase 26: DQN Feature Extraction (if DQN enabled)
  │    Input: TickContext (last 100 seconds of ticks)
  │    Extract: OHLCV, orderbook, volatility, time-of-day, sector
  │    Output: feature_tensor [batch, 20, 32]
  │    (Store in memory for Phase 27 training)
  │
  ├─→ Phase 7: Confidence Scorer
  │    Input: TickContext, regime, vol_scalar
  │    Calculate: 8-indicator weighted score
  │    IF DQN enabled:
  │      - Get DQN inference (latency <50ms)
  │      - Blend DQN + 8-indicator confidence
  │    Output: confidence_score [0-100]
  │
  ├─→ Phase 4: White Reality Check
  │    Input: confidence_score, signal history
  │    Calculate: Deflated Sharpe Ratio
  │    Gate: IF DSR < 1.0 → SKIP (don't trade)
  │          IF DSR ≥ 1.0 → PROCEED
  │    Output: is_significant (bool)
  │
  ├─→ Phase 8: Pre-Conditions Gate
  │    Check: ISA active? Margin = 0? Queue < 50? CB = GREEN?
  │    Gate: IF any check fails → QUEUE order, retry (max 10x)
  │           IF all pass → PROCEED
  │    Output: PASS/FAIL
  │
  ├─→ Phase 9: Position Sizer
  │    Input: kelly_max, regime, vol_scalar, confidence
  │            fx_adjustment (Phase 30), geo_multiplier (Phase 31)
  │    Calculate: Kelly 12-factor
  │    Apply: Leverage prioritization (NVDA→NVD3.L, etc)
  │    Apply: FX adjustment (if EUR/AUD/JPY)
  │    Apply: Geopolitical multiplier (0.7x MEDIUM, 0.3x HIGH)
  │    Output: position_size, symbol (with leverage)
  │
  ├─→ Phase 10: Execution Quality
  │    Input: symbol, position_size, regime
  │    Estimate: slippage per market
  │    Calc: entry_timing_score
  │    Output: expected_fill_price, timing_score
  │
  ├─→ Phase 30: FX Manager (if non-LSE)
  │    Input: symbol currency
  │    Check: Is FX hedge in place?
  │    Calc: Daily FX cost
  │    Output: fx_adjustment (already applied in Phase 9)
  │
  ├─→ Phase 31: Geopolitical Risk Manager
  │    Input: market (EURONEXT/ASX/TSE)
  │    Check: Current risk level (LOW/MEDIUM/HIGH/HALT)
  │    Output: position_multiplier (0.7x or 0.3x if elevated)
  │             (already applied in Phase 9)
  │
  ├─→ Phase 2: ISA Auditor (Every 5 min, not every tick)
  │    Check: Margin = 0? ISA eligible? No halts?
  │    Gate: PASS or FAIL (+ HALT if fail)
  │
  ├─→ Phase 3: Compliance Gates
  │    Check: Margin? Spread? Trading halts?
  │    Gate: PASS (allow order) or FAIL (reject order)
  │
  ├─→ Phase 15: Order Router
  │    Input: symbol, position_size, regime
  │    IF Phase 8 PASS AND Phase 4 PASS AND Phase 3 PASS:
  │      → Format order (MOC/MTL per timing)
  │      → Submit to IBKR via ibapi
  │      → Log order_id, timestamp
  │    Output: order_id, fill_confirmation
  │
  ├─→ Phase 19: Risk Manager (immediately after submission)
  │    Input: filled order, entry_price, regime
  │    Set: Stop loss (regime-dependent)
  │    Track: Daily portfolio heat
  │    Check: L1 (-1.5%), L2 (-2.5%), L3 (-4.0%) circuit breakers
  │    Output: stop_loss_price, circuit_breaker_status
  │             (stops are "shadow" in exit_engine.rs)
  │
  ├─→ Phase 20: Reconciliation Auditor (Every 5 min)
  │    Check: Holdings match IBKR?
  │    Check: Margin still zero?
  │    Check: All positions ISA-eligible?
  │    Output: PASS or FAIL + HALT
  │
  ├─→ Phase 21: Position Manager
  │    Track: Current position state
  │    Update: Live P&L per position
  │    Close: At targets, stops, time-based exits
  │    Output: Updated portfolio state (to database)
  │
  └─→ Database Logging
       Log:
       - Order submission (Phase 15)
       - Risk events (Phase 19)
       - Reconciliation status (Phase 20)
       - Position updates (Phase 21)
       - ISA audit trail (Phase 2, 20)
       - Geopolitical events (Phase 31)
```

### NIGHTLY CYCLE (22:00-23:50 UTC)

```
22:00 UTC: OUROBOROS BEGIN
  │
  ├─→ Phase 23: Performance Attribution (22:00-22:10)
  │    Input: All trades executed today (from database)
  │    Calculate:
  │      - Win rate per regime
  │      - Return attribution (signal, timing, regime)
  │      - Confidence score vs actual outcome correlation
  │    Output: Metrics to database
  │
  ├─→ Phase 26: DQN Data Preparation (parallel)
  │    Input: Daily trades + P&L (ground truth labels)
  │    Prepare: Training batch (5,000 transitions if volume high)
  │    Output: Ready for Phase 27
  │
  ├─→ Phase 27: DQN Model Retraining (optional, if volume sufficient)
  │    Input: Daily batch of transitions
  │    Train: DQN model for 100 episodes
  │    Validate: Check if new model > old model
  │    Output: Updated model (or keep old if degraded)
  │
  ├─→ Phase 22: DQN Signal Weighting (22:10-22:20)
  │    Input: Win rates per regime (from Phase 23)
  │    Logic:
  │      IF regime_wr < 40% → raise threshold +0.5
  │      IF regime_wr > 50% → lower threshold -0.25
  │    Update: Indicator weights, leverage multipliers
  │    Output: New parameters to database
  │
  ├─→ Phase 24: ML Adaptation (22:10-22:20)
  │    Input: Phase 22 updated parameters
  │    Logic: Adjust confidence thresholds per regime
  │    Output: Updated thresholds (apply tomorrow)
  │
  ├─→ Phase 25: Live Orchestrator (22:50-23:00)
  │    Commit: All Phase 22-24 changes to database
  │    Prepare: Universe scan with new thresholds
  │    Verify: Heartbeat (system ready for 08:00 LSE open)
  │    Output: Confirm ready for next day
  │
  └─→ Universe Scan (parallel with Phases 22-25)
       Input: All 1,770+ assets
       For each asset:
         - Calculate signal strength (8-indicator)
         - Calculate regime fit (expected WR)
         - Calculate volatility fit
         - Apply FX adjustment (if EUR/AUD/JPY)
         - Apply geopolitical adjustment (if TSE/EURONEXT/ASX risk elevated)
         - Estimate position size (Kelly)
       Tier by signal strength:
         - HIGH_CONVICTION (top 50)
         - STANDARD (51-200)
         - WATCHLIST (201-500)
       Output: Saved to database, ready for Phase 9 (Position Sizer) next day
```

### DECISION GATES (Critical Junctures)

```
GATE 1: Base System Validation (End Week 9)
├─ Condition: 100+ paper trades executed
├─ Condition: Win rate ≥40% (all regimes)
├─ Condition: Max drawdown <-8%
├─ Condition: ISA audit clean (100% compliant)
├─ Condition: Data feed uptime ≥99.9%
├─ Condition: Ouroboros nightly cycle stable
└─ Decision:
   IF all pass → PROCEED TO LIVE WEEK 10
   IF any fail → CONTINUE PAPER TRADING (re-validate)

GATE 2: DQN Validation (End Week 9)
├─ Condition: Backtest Sharpe ≥1.0
├─ Condition: Walk-forward Sharpe ≥0.8
├─ Condition: Deflated Sharpe ≥0.7
├─ Condition: Per-regime Sharpe ≥0.5 (all 5 regimes)
├─ Condition: Paper trading accuracy ≥55%
├─ Condition: Inference latency <50ms
├─ Condition: Memory/CPU stable (no leaks)
└─ Decision:
   IF all pass → DQN_APPROVED = True
                 Phase 29 integrates DQN into Phase 7 (Confidence Scorer)
                 Switch to dual-signal (8-ind + DQN, execute 8-ind)
   IF any fail → DQN_APPROVED = False
                 Continue with 8-indicator only
                 (Both paths proceed to Euronext)

GATE 3: Euronext Validation (End Week 14)
├─ Condition: 50+ paper trades executed
├─ Condition: Win rate ≥40%
├─ Condition: Data feed uptime ≥99.8%
├─ Condition: Execution latency <500ms
├─ Condition: FX costs ≈ 15 bps/month verified
└─ Decision:
   IF all pass → EURONEXT_APPROVED = True
                 Phase 31 (Euronext trading) goes live
   IF any fail → EURONEXT_APPROVED = False
                 Keep in paper, investigate
                 (Cannot go live without validation)

GATE 4: ASX Validation (End Week 18)
├─ Condition: 30+ paper trades executed
├─ Condition: Win rate ≥35% (lower due to overnight/FX)
├─ Condition: Geopolitical monitoring stable (<1 alert/day)
├─ Condition: Position multiplier logic verified
├─ Condition: Overnight monitoring system robust
└─ Decision:
   IF all pass → ASX_APPROVED = True
                 Phase 32 (ASX trading) goes live
   IF any fail → ASX_APPROVED = False
                 Keep ASX in paper only

GATE 5: TSE Optional (Week 19+)
├─ Condition: DQN_APPROVED = True (DQN validated, proved value)
├─ Condition: DQN live win rate ≥45% for 4+ weeks
├─ Condition: Geopolitical manager proved valuable (multipliers helped)
├─ Condition: Infrastructure stable (no major incidents)
└─ Decision:
   IF all pass → TSE_APPROVED = True
                 Phase 32D (TSE trading) goes live
                 Use DQN for regime-specific learning (Japanese patterns)
   IF any fail → TSE_APPROVED = False
                 Skip TSE, focus on scaling to £100M AUM
                 (Not critical for success)
```

---

## III. SPECIFIC FILE MODIFICATIONS (All Wires Specified)

### NEW FILES TO CREATE

```
RUST ENGINE (nzt48-aegis-v2/rust_core/src/):
├─ feed_manager.rs (200 lines)
│  Purpose: Route ticks by market, handle failover
│  Wires: Phase 0 → Phases 5, 26, 30, 31
│  Dependencies: types.rs (TickContext struct)
│  Tests: [TEST] IBKR → Phase 5, [TEST] Stale failover
│
├─ dqn_signal_weighting.rs (EXISTING, 66 lines, ACTIVATE)
│  Purpose: Track DQN performance, update weights nightly
│  Currently stub, needs activation in Phase 29
│  Wires: Phase 22 → Phases 5, 7, 9
│  Tests: [TEST] Q-values tracked, [TEST] Epsilon decay
│
├─ dqn_monitoring.rs (150 lines, NEW)
│  Purpose: Monitor DQN health (latency, Q-value drift, accuracy)
│  Wires: Phase 29 → Monitoring dashboard + alerts
│  Tests: [TEST] Latency <50ms, [TEST] Drift alert at ±0.2
│
├─ fx_manager.rs (150 lines, NEW)
│  Purpose: Track FX exposure, calculate hedging costs
│  Wires: Phase 30 → Phases 9, 15, 19
│  Tests: [TEST] EUR cost 15 bps/mo, [TEST] Position adjusted
│
├─ geopolitical_risk_manager.rs (200 lines, NEW)
│  Purpose: Monitor news API, update risk levels, apply multipliers
│  Wires: Phase 31 → Phases 9, 15, database
│  Tests: [TEST] "sanctions" → HIGH risk, [TEST] Multiplier applied
│
├─ execution_quality.rs (120 lines, NEW)
│  Purpose: Slippage modeling, entry timing optimization
│  Wires: Phase 10 → Phases 15, monitoring
│  Tests: [TEST] Slippage per market, [TEST] Entry timing score
│
├─ dsr_calculator.rs (100 lines, NEW)
│  Purpose: Compute Deflated Sharpe Ratio for signal validation
│  Wires: Phase 4 → Phases 8, 15, database
│  Tests: [TEST] DSR > 1.0 → PASS, [TEST] Bootstrap CI
│
└─ (All other phases use EXISTING files with modifications below)

PYTHON BRAIN (nzt48-aegis-v2/python_brain/brain/):
├─ strategies/dqn_transformer_v1.py (400 lines, NEW)
│  Purpose: DQN value network + Transformer encoder architecture
│  Wires: Phase 27 (training) → Phase 29 (inference)
│  Classes:
│    - TransformerPriceEncoder (80-dim hidden)
│    - DQNValueNetwork (Dueling DQN)
│    - DQNStrategy.evaluate() (returns confidence, Q-values)
│  Tests: [TEST] Forward pass works, [TEST] Q-values in [-1, +1]
│
├─ ml/data/extract_candles.py (100 lines, NEW)
│  Purpose: OHLCV extraction from IBKR ticks
│  Wires: Phase 26A → Phase 26E (dataset assembly)
│  Tests: [TEST] 5s/30s/60s candles extracted, [TEST] Volume summed
│
├─ ml/data/extract_orderbook.py (80 lines, NEW)
│  Purpose: Bid-ask microstructure features
│  Wires: Phase 26B → Phase 26E
│  Tests: [TEST] Top 5 levels extracted, [TEST] Spread calculated
│
├─ ml/data/extract_volatility.py (80 lines, NEW)
│  Purpose: Realized vol, Amihud, GARCH metrics
│  Wires: Phase 26C → Phase 26E
│  Tests: [TEST] Realized vol computed, [TEST] Amihud ratio
│
├─ ml/data/extract_context.py (80 lines, NEW)
│  Purpose: Time-of-day + sector momentum features
│  Wires: Phase 26D → Phase 26E
│  Tests: [TEST] 9 time buckets, [TEST] Cross-sectional momentum
│
├─ ml/data/dataset.py (120 lines, NEW)
│  Purpose: PyTorch dataset assembly + normalization + splits
│  Wires: Phase 26E → Phase 27 (training)
│  Tests: [TEST] Dataset shape correct, [TEST] Classes balanced
│
├─ ml/train_dqn.py (300 lines, NEW)
│  Purpose: DQN training loop (100k episodes, epsilon decay, replay buffer)
│  Wires: Phase 27C → Phase 28 (validation)
│  Tests: [TEST] Training loss decreases, [TEST] Win rate improves
│
├─ ml/validate_walkforward.py (150 lines, NEW)
│  Purpose: Walk-forward validation (4 folds, per-regime testing)
│  Wires: Phase 28A → Phase 28B-C
│  Tests: [TEST] Walk-forward Sharpe ≥0.8, [TEST] Degradation <30%
│
├─ ml/validate_deflated_sharpe.py (100 lines, NEW)
│  Purpose: Deflated Sharpe ratio calculation (multiple comparisons)
│  Wires: Phase 28B → Go/No-Go gate
│  Tests: [TEST] DSR ≥0.7 required for approval
│
├─ ml/validate_per_regime.py (100 lines, NEW)
│  Purpose: Per-regime performance validation
│  Wires: Phase 28C → Go/No-Go gate
│  Tests: [TEST] All regimes Sharpe ≥0.5
│
├─ ml/validate_paper_trading.py (80 lines, NEW)
│  Purpose: Live paper trading validation (dual-signal accuracy)
│  Wires: Phase 28D → Go/No-Go gate
│  Tests: [TEST] DQN accuracy ≥55% on live ticks
│
└─ scripts/nightly_universe_scan.py (EXTENDED, 200 lines)
   Purpose: Daily asset ranking (signal strength, regime fit, FX adj, geo adj)
   Wires: Input from Phases 23-24 (updated params)
           Output to database (High Conviction / Standard / Watchlist)
           Used by Phase 9 (Position Sizer) next day
   Tests: [TEST] All 1,770 assets scored, [TEST] Top 50 ranked

CONFIG FILES (nzt48-aegis-v2/config/):
├─ contracts.toml (EXTENDED to 1,000+ lines)
│  Add sections:
│  [euronext_core] - 30 European stocks
│  [asx_core] - 25 Australian stocks
│  [tse_core] - 50 Japanese stocks (optional)
│  Each with: ticker, sector, leverage, trading_hours, fx_cost, geopolitical_risk
│
├─ config.toml (EXTENDED)
│  Add sections:
│  [dqn_model] - DQN inference paths, latency target
│  [geopolitical] - NewsAPI key, risk thresholds
│  [fx_hedging] - FX costs per currency, hedge method
│  [euronext] - Trading hours CET, liquidity params
│  [asx] - Trading hours AEDT, overnight params
│  [tse] - Trading hours JST, settlement T+3
│
└─ initial_universe.toml (EXTENDED)
   Expand from 92 tickers to 1,770+ with all markets
```

### EXISTING FILES TO MODIFY

```
RUST ENGINE (nzt48-aegis-v2/rust_core/src/):

ibkr_broker.rs (MODIFY):
  Add market-specific subscriptions
  Ensure Euronext/ASX/TSE market data flows via IBKR
  Wire to Phase 0 Feed Manager

clock.rs (MODIFY ~90 lines):
  Add functions:
    - is_euronext_open() → CET time check
    - is_asx_open() → AEDT time check
    - is_tse_open() → JST time check
    - market_settlement_time() → T+2 vs T+3
  Wires: Phase 30 → Phases 5, 15, 25 (timing logic)

universe.rs (MODIFY ~100 lines):
  Extend RouteResult enum to include market (LSE/NYSE/EURONEXT/ASX/TSE)
  Extend filtering: market-specific halts, illiquidity, FX cost
  Wires: Phase 0 → Phases 5, 9, 30, 31

risk_arbiter.rs (MODIFY ~150 lines):
  Extend CHECK 4: White Reality Check (DSR validation)
  Extend CHECK 5: Pre-Conditions Gate (add DQN check)
  Extend CHECK 19: Reconciliation Auditor (geopolitical flag check)
  Add phase-by-phase check logging
  Wires: Phases 2-20 (all risk gates)

python_bridge.rs (MODIFY ~50 lines):
  If DQN enabled: Pass DQN signal to subprocess
  Extend BrainSignal struct to include dqn_confidence
  Wires: Phase 29 (DQN inference) ← → Phase 7 (Confidence Scorer)

engine.rs (MODIFY ~50 lines):
  Initialize Phase 30 (FX Manager) at startup
  Initialize Phase 31 (Geopolitical Risk Manager) at startup
  Call Phase 0 (Feed Manager) for every tick
  Wires: All phases to main event loop

types.rs (MODIFY ~50 lines):
  Add TickContext struct:
    - market (LSE/NYSE/EURONEXT/ASX/TSE)
    - symbol
    - bid, ask, price, volume
    - timestamp
  Add Position struct extensions:
    - currency (GBP/EUR/AUD/JPY)
    - fx_cost_daily
    - geo_multiplier
  Add Order struct extensions:
    - market
    - slippage_estimate

PYTHON BRAIN:

strategies/vanguard_sniper.py (MODIFY ~50 lines):
  Import DQNStrategy (if enabled)
  Try/catch DQN inference
  Fallback to 8-indicator if DQN errors
  Log both signals (dual-signal logging)
  Blend confidence if DQN live
  Wires: Phase 7 (Confidence Scorer) ← Phase 29 (DQN inference)

strategies/apex_scout.py (MODIFY ~30 lines):
  Add Euronext-specific volatility adjustments
  Add ASX-specific overnight mode
  Wires: Phase 26 (feature extraction) → Phase 27 (DQN training)

sizing/kelly_12factor.py (MODIFY ~30 lines):
  Add factor 13: DQN uncertainty penalty
  Add FX adjustment (multiply by (1 - fx_cost / daily_target))
  Add geopolitical multiplier (apply before final size)
  Wires: Phase 30 (FX adjustment) + Phase 31 (geo multiplier)

config.py (MODIFY ~20 lines):
  Add DQN_ENABLED flag
  Add DQN model paths
  Add EURONEXT_ENABLED, ASX_ENABLED, TSE_ENABLED flags
  Add Geopolitical API key

DATABASE:

monitoring.sql (NEW schema):
  Tables:
    - dqn_metrics (inference_latency, q_value_mean, q_value_std, accuracy)
    - geopolitical_events (timestamp, market, risk_level, news_snippet)
    - fx_costs (daily, by_currency)
    - trades (order_id, timestamp, symbol, side, size, fill_price, pnl, regime, confidence, strategy)
    - risk_events (timestamp, event_type, severity, action_taken)
    - model_params (timestamp, indicator_weights, thresholds, leverage_multipliers)
    - performance_attribution (trade_id, signal_contribution, timing_contribution, regime_contribution)

  Indexes:
    - CREATE INDEX idx_trades_timestamp ON trades(timestamp)
    - CREATE INDEX idx_geopolitical_market ON geopolitical_events(market)
    - CREATE INDEX idx_dqn_metrics_time ON dqn_metrics(timestamp)
```

---

## IV. END-TO-END TEST CASES (Prove All Wires Work)

### TEST SUITE 1: Base System (Phases 1-25)

```
[TEST] Boot: All phases load without error
  [STEP] Load config files → Phase 1 init
  [STEP] Load ISA whitelist → Phase 2 init
  [STEP] Connect IBKR → Phase 0 ready
  [VERIFY] System online, listening for ticks

[TEST] Tick flow: LSE ticker reaches Phase 7 (Confidence Scorer)
  [STEP] IBKR sends LVMH.PA tick
  [STEP] Phase 0 routes to Phase 5 (LSE market)
  [STEP] Phase 5 detects TRENDING_UP regime
  [STEP] Phase 6 scaler = 1.2x (quiet vol)
  [STEP] Phase 26 extracts features (if DQN enabled)
  [STEP] Phase 7 calculates 8-indicator score = 7.2
  [VERIFY] Confidence 7.2 reaches Phase 4 (DSR check)

[TEST] Gate flow: DSR check passes → order routed
  [STEP] Phase 4 calc DSR = 1.15 (>1.0 ✓)
  [STEP] Phase 8 checks all pre-conds → PASS
  [STEP] Phase 9 sizes position = 150 shares
  [STEP] Phase 10 estimates slippage = 15 bps
  [STEP] Phase 3 compliance check → PASS
  [STEP] Phase 15 submits order to IBKR
  [VERIFY] Order filled, logged to Phase 21 (Position Manager)

[TEST] Risk gate: ISA auditor blocks non-ISA asset
  [STEP] Signal fires for TSM.US (US stock, not ISA)
  [STEP] Phase 2 ISA auditor runs (every 5 min)
  [STEP] Phase 2 checks: IS TSM.US ISA eligible? NO
  [STEP] Phase 2 output: FAIL
  [STEP] Phase 15 blocks order (ISA gate active)
  [VERIFY] No order submitted, alert logged

[TEST] Circuit breaker: L1/L2/L3 cascade on drawdown
  [STEP] Daily P&L = -1.5% (L1 threshold)
  [STEP] Phase 19 triggers L1: set alert, stop new positions
  [STEP] Daily P&L = -2.5% (L2 threshold)
  [STEP] Phase 19 triggers L2: reduce all positions 50%
  [STEP] Daily P&L = -4.0% (L3 threshold)
  [STEP] Phase 19 triggers L3: FLATTEN ALL POSITIONS
  [VERIFY] All positions closed, trading halted, manual review required

[TEST] Nightly ouroboros: Phase 23 → 24 → 25 chain
  [STEP] 22:00 UTC: Phase 23 fetches 500 trades from DB
  [STEP] Phase 23 calc: TRENDING_UP regime WR = 55% (good)
  [STEP] Phase 23 calc: RANGE regime WR = 42% (ok)
  [STEP] Phase 23 calc: RISK_OFF regime WR = 25% (bad!)
  [STEP] Phase 22 logic: RISK_OFF threshold + 0.5 (reduce signal quality)
  [STEP] Phase 24 logic: Adjust leverage -10% in RISK_OFF regime
  [STEP] Phase 25: Commit all to DB, heartbeat OK
  [VERIFY] New params live at 08:00 LSE open
```

### TEST SUITE 2: DQN System (Phases 26-29)

```
[TEST] Feature extraction: Phase 26 → Phase 27
  [STEP] Feed Manager sends 100 LSE ticks (Phase 0)
  [STEP] Phase 26A extracts 5s/30s/60s OHLCV candles
  [STEP] Phase 26B extracts top 5 bid/ask, slope, volume
  [STEP] Phase 26C computes realized vol, Amihud ratio
  [STEP] Phase 26D time-of-day + sector momentum
  [STEP] Phase 26E normalizes, assembles tensor [1, 20, 32]
  [VERIFY] Feature tensor ready for Phase 27 DQN inference

[TEST] DQN inference: Model predicts action in <50ms
  [STEP] Phase 27 loads pre-trained DQN model (regime-specific)
  [STEP] Phase 27 forward pass on feature tensor
  [STEP] Q-values output: [SELL, HOLD, BUY] = [-0.1, 0.2, 0.8]
  [STEP] Argmax → action = BUY, q_value = 0.8
  [STEP] Map to confidence: (0.8 * 25) + 50 = 70
  [STEP] Measure latency: 32ms (< 50ms ✓)
  [VERIFY] DQN confidence 70 sent to Phase 7

[TEST] DQN fallback: If model crashes, revert to 8-indicator
  [STEP] Phase 29 DQN inference throws error (GPU OOM)
  [STEP] Exception caught, fallback triggered
  [STEP] Phase 7 confidence = 8-indicator score (no blend)
  [STEP] Alert logged: "DQN inference failed, reverted to 8-indicator"
  [STEP] Trade executes on 8-indicator signal
  [VERIFY] System stable, no loss of trading capability

[TEST] Walk-forward validation: DQN passes gate criteria
  [STEP] Phase 28 splits data (Q1-Q2 train, Q3 test)
  [STEP] Phase 28 backtest Sharpe = 1.1
  [STEP] Phase 28 walk-forward Sharpe = 0.82
  [STEP] Phase 28 deflated Sharpe = 0.75 (>0.7 required ✓)
  [STEP] Phase 28D paper trading accuracy = 56% (>55% required ✓)
  [STEP] Latency test = 38ms (<50ms required ✓)
  [VERIFY] All 7 gates pass → DQN_APPROVED = True

[TEST] Dual-signal mode: 8-indicator + DQN comparison
  [STEP] Phase 7 calculates 8-indicator confidence = 6.8
  [STEP] Phase 29 DQN inference confidence = 7.2
  [STEP] Phase 7 logs both: "8-ind=6.8, DQN=7.2, executing on 8-ind"
  [STEP] Trade executed on 8-indicator signal (primary)
  [STEP] After 2 weeks, DQN win rate = 42% (>42% required ✓)
  [STEP] Phase 29 promotes DQN to primary signal
  [VERIFY] DQN now drives orders, 8-indicator becomes fallback
```

### TEST SUITE 3: Global Markets (Phases 30-32)

```
[TEST] FX Manager: EUR/AUD/JPY exposure tracked, position adjusted
  [STEP] Phase 30 tracks EUR exposure = £50k
  [STEP] Phase 30 calculates EUR hedge cost = £50k * 0.0015 / 252 = £0.30/day
  [STEP] Daily target return = 0.45%, so daily target £45
  [STEP] FX cost / target = £0.30 / £45 = 0.67% → adjustment = 0.9933x
  [STEP] Phase 9 position size reduced by 0.67%
  [VERIFY] Position size properly adjusted for FX cost

[TEST] Geopolitical Risk: NEWS API triggers position multiplier
  [STEP] Phase 31 NewsAPI scan at 07:00 UTC (daily)
  [STEP] Keywords: "france sanctions" found in 3 articles (high volume)
  [STEP] Sentiment score: 0.78 (HIGH RISK threshold = 0.7)
  [STEP] Phase 31 sets EURONEXT risk = HIGH
  [STEP] Position multiplier = 0.3x (70% reduction)
  [STEP] Phase 9 applies 0.3x to all Euronext positions
  [VERIFY] Euronext position sizes reduced 70%, risk controlled

[TEST] Euronext routing: CAC ticker flows through Phase 31
  [STEP] IBKR sends CAC.PA tick
  [STEP] Phase 0 routes to EURONEXT market
  [STEP] Phase 30 FX check: EUR→GBP hedge required
  [STEP] Phase 31 geopolitical check: risk = LOW
  [STEP] Phase 9 sizes position = 200 shares (no FX/geo reduction)
  [STEP] Phase 15 routes to IBKR for Euronext execution
  [VERIFY] CAC trade executes, slippage ~25 bps (Euronext spec)

[TEST] ASX overnight mode: Trade executes 23:50-05:00 UTC
  [STEP] Phase 0 detects ASX market (time 23:55 UTC)
  [STEP] Phase 5 regime detection (ASX-specific vol)
  [STEP] Phase 9 position sizer applies AUD FX cost (-25 bps/mo)
  [STEP] Phase 31 geopolitical: ASX low risk → 1.0x
  [STEP] Phase 15 submits ASX order
  [STEP] Order fills at 00:15 UTC (ASX 10:15 local)
  [VERIFY] Position opens overnight, monitored until 05:00 UTC close

[TEST] TSE conditional: Only enables if DQN validated
  [STEP] GATE 5 evaluation: DQN_APPROVED = False
  [STEP] TSE trades BLOCKED (not enabled)
  [STEP] Later: Re-train DQN, validation passes, DQN_APPROVED = True
  [STEP] TSE trades ENABLED (using DQN for regime learning)
  [STEP] DQN live WR = 43% (<45% required)
  [STEP] TSE trades BLOCKED again (gate re-evaluated weekly)
  [VERIFY] TSE gating logic works correctly

[TEST] Trading halt detection: Reduces positions if >3 halts/hour
  [STEP] Phase 31 monitors market-specific halt feeds (LSE, Euronext, ASX)
  [STEP] 5 trading halts detected on Euronext in 1 hour
  [STEP] Phase 31 triggers: reduce Euronext positions 50%
  [STEP] After 60 min with <1 halt, restore to normal size
  [VERIFY] Halt protection prevents execution whipsaw
```

---

## V. DEPLOYMENT CHECKLIST (All Wires Verified)

### PRE-DEPLOYMENT (Week 0)

- [ ] All 32 phases designed (Phases 1-25 base, 26-29 DQN, 30-32 global)
- [ ] All file modifications specified (new files + existing file changes)
- [ ] All wires explicitly defined (every input → output connection)
- [ ] All test cases written (proof of concept per phase)
- [ ] All gates defined (go/no-go criteria clear)
- [ ] Database schema finalized (tables, indexes)
- [ ] Config files extended (Euronext, ASX, TSE, DQN params)

### WEEK 1 STARTUP

- [ ] EC2 provisioned (2 vCPU, 4GB RAM, 100GB SSD)
- [ ] Docker Compose (Rust engine, IBKR, Redis, PostgreSQL)
- [ ] Phase 0 (Feed Manager) connects to IBKR
- [ ] Phase 1 (Kelly) initialized, limits set
- [ ] Phase 2 (ISA Auditor) loaded ISA whitelist
- [ ] Phase 5 (Regime Detection) online, HMM loaded
- [ ] Phases 1-10 live (base signal pipeline)
- [ ] Database initialized, audit trail logging starts
- [ ] Paper trading begins with 8-indicator system

### WEEK 3 DQN START

- [ ] Phase 26 (Feature extraction) pipeline complete
- [ ] 2 years LSE historical data loaded
- [ ] Phase 27 DQN training loop running
- [ ] Backtest validation in progress

### WEEK 6 VALIDATION

- [ ] Phase 28 walk-forward validation running
- [ ] All 4 folds tested, DSR ≥0.7 target
- [ ] Phase 28D paper trading dual-signal comparison

### WEEK 9 DECISION

- [ ] Base system gate passed (100+ trades, WR ≥40%, drawdown OK)
- [ ] DQN gate decision made (if validation passed)
- [ ] Phase 30 infrastructure online (FX, geopolitical managers)
- [ ] Euronext feed tested (latency, data quality)
- [ ] ASX feed tested (overnight mode)

### WEEK 10 GO-LIVE

- [ ] Go live with base system + optional DQN
- [ ] Start with £10k, 5-10% max position size
- [ ] Daily monitoring active
- [ ] Weekly performance reviews

### WEEK 13 EURONEXT DEPLOYMENT

- [ ] Euronext go/no-go gate passed (50+ trades, WR ≥40%)
- [ ] Go live with Euronext (30 European stocks)
- [ ] FX hedging active (15 bps/month cost)

### WEEK 18 ASX + GEOPOLITICAL

- [ ] ASX go/no-go gate passed (30+ trades, WR ≥35%)
- [ ] Go live with ASX (25 Australian stocks, overnight mode)
- [ ] Geopolitical monitoring live (news API scanning daily)
- [ ] Position multipliers auto-adjust per risk level

### WEEK 19+ OPTIONAL TSE

- [ ] TSE optional gate evaluated (DQN validated + geo manager stable)
- [ ] If gates pass: Deploy TSE (50 Japanese stocks, DQN regime learning)
- [ ] If gates fail: Skip TSE, focus on scaling

---

## FINAL APPROVAL GATE

**This wiring specification is COMPLETE and READY FOR APPROVAL.**

All 32 phases fully wired:
- ✅ Every input explicitly sourced
- ✅ Every output explicitly delivered
- ✅ Every dependency explicitly stated
- ✅ Every gate explicitly defined
- ✅ Every test case explicitly specified
- ✅ Zero orphan logic, zero ambiguous handoffs

**Ready to implement starting Monday, March 17, 2026?**

**APPROVE TO PROCEED** ___________

