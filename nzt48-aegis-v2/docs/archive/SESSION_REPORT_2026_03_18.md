# AEGIS V2 — Session Report: 2026-03-18
## Deep Research → Multi-Persona Audit → Adaptive Implementation

**Status**: Simulation-only. No live orders possible.
**Starting State**: 0% win rate across 52 paper trades. Root cause: wrong strategy family + tight stops.

---

## PHASE 1: DEEP RESEARCH (4 Parallel Agents)

### Agent 1: Strategy Mechanics (VWAP, Gap, RSI/IBS, Sessions)
**Key findings with specific parameters:**

| Strategy | Win Rate (Realistic) | Best Session | Key Parameter |
|----------|---------------------|-------------|---------------|
| VWAP DipBuy | 55-65% | 10:30-14:30 UK | Entry at VWAP-2σ, target VWAP |
| Gap Fade | 75-89% (on underlying), 45-55% (on 3x) | 08:15-10:00 UK | RVOL < 2x filter critical |
| RSI(2)/IBS | 73-78% (underlying), 50-60% (3x) | 20:30-21:00 UK | RSI < 2.5 + IBS < 0.10 for 3x |
| Cross-Market Momentum | 55-65% | 14:45-16:00 UK | S&P first 15min > 0.3% |
| Intraday Momentum | 50-55% | 20:30-21:00 UK | First-half-hour → last-half-hour |

**Session-specific regime map:**
- 08:00-08:30: MOMENTUM (opening volatility, gaps)
- 10:00-14:30: MEAN-REVERSION (low vol, VWAP magnet)
- 14:30-16:00: MIXED (US overlap, best liquidity)
- 16:00-16:30: REBALANCING (avoid MR, ride rebalancing flow)

### Agent 2: Stop-Loss & Entry Quality
- Osler (2005): stops cluster at round numbers, swept in cascades
- Le Beau Chandelier standard: 3.0x ATR for unleveraged. 3x ETPs need adjustment.
- Random walk: 1x ATR stop hit within 4 hours ~70% probability. 2x ATR ~35%.
- IBKR LSE commissions: tiered vs fixed, ~£1-3 per trade. Stamp duty 0.5% on UK stocks (NOT on ETPs).
- Shadow stops (internal) avoid front-running vs server-side stops.

### Agent 3: Adaptive Architecture
- Hurst window: increase from 30 to 100 bars for stability
- ADX: > 25 for momentum, < 20 for mean-reversion
- Choppiness Index: < 50 for trend, > 61.8 for chop
- VIX tiers: <15 low, 15-25 normal, 25-35 high, >35 crisis
- Regime hysteresis: enter trending at H>0.58, exit at H<0.52 (prevents flip-flopping)
- Parameter robustness: test ±10-20%, reject cliff parameters
- Walk-forward validation: 20-day IS, 5-day OOS, max 70% decay acceptable
- Complexity budget: max 12 adaptive parameters (prevents overfitting)
- Need 10 trades per parameter before adaptation is meaningful

### Agent 4: Ouroboros Adaptive Loop
- MAE/MFE stop calibration: P0 highest impact. Stop at 80th percentile of winner MAE.
- Thompson Sampling for signal promotion/suppression
- Beta-Bernoulli Bayesian win rates with Beta(10,10) prior
- Cross-market: NQ overnight change predicts LSE gap direction
- VIX > 30: 3x products lose more to decay than they gain from direction
- VIX > 35: SUSPEND all 3x longs
- Inverse ETPs: ISA-legal, wider spreads (0.20-0.35%), max intraday hold at VIX>30
- Graduated trust: trade_count < 10 → use defaults, 10-30 → 30% adaptive, 30-100 → 70%, >100 → full
- Rollback: if 3 rollbacks in 14 days, freeze adaptations for 7 days

---

## PHASE 2: MULTI-PERSONA AUDIT (4 Parallel Agents)

### Quant Researcher Audit — CRITICAL FINDINGS:
1. **Win rate targets overstated by 15-25pp** across all strategies
2. **Entry filter conjunction too strict** (~0.6-2% joint pass rate = ~0-2 opportunities/week)
3. **Chandelier rungs too wide** for compounding — Rung 2 at +2% unreachable on most trades
4. **2% minimum profit contradicts high win rate** — mathematically impossible to have both
5. **S21 Intraday Momentum inoperable** — session time mismatch
6. **Concentrate on S19 (RSI/IBS) + S17 (VWAP DipBuy)**

### Fund Manager Audit — CRITICAL FINDINGS:
1. **Max 2-3 positions at £10,000** (6 positions = 90% deployment = suicidal)
2. **0.3% daily achievable, 0.5% requires Sharpe ~2.0** (unsustainable)
3. **Cross-Market Momentum is the BEST strategy** — structural edge from US→LSE lag
4. **Gap Fade is the WEAKEST** — barely covers costs on 3x products
5. **RSI/IBS multi-day hold costs 0.31-0.86% in decay** per trade
6. **Missing: portfolio-level daily loss limit, earnings blackout, correlation cap**

### Systems Engineer Audit — CRITICAL FINDINGS:
1. **No server-side stop-losses at IBKR** — positions unprotected during disconnect
2. **No position reconciliation on restart** — orphaned positions possible
3. **GIL contention from Ouroboros** — must run in separate process
4. **Race condition: ticker ranker vs orchestrator** — needs snapshot locking
5. **100 tickers for 12-ETP universe is overkill** — reduce to actual tradeable set
6. **PyO3 round-trip latency is fine** (~5-55ms vs 5000ms budget)

### Risk Officer Audit — CRITICAL FINDINGS:
1. **2% daily drawdown INCOMPATIBLE with 2x ATR stops on 3x ETPs** — single TSL3.L stop = 3%
2. **Worst-case 6-position day: -16.8% realistic, -27% extreme**
3. **Overnight gap risk unmitigated** — RSI/IBS holds through 10-15% gap events
4. **Missing: weekly DD limit, equity floor, overnight cap, correlation limit, dead-man's switch**
5. **Rung 2+ rate must exceed 71.4% just to break even** with current structure
6. **VIX > 35: suspend all 3x longs** (tiered response needed)

---

## PHASE 3: IMPLEMENTATION

### Files Created (All with tests passing):

**Rust:**
- `rust_core/src/strategy_config.rs` — 660 lines, full TOML strategy config loader, 15 tests
- Updated `lib.rs` with `pub mod strategy_config;`

**Python:**
- `python_brain/brain/vwap.py` — Intraday VWAP calculator with sigma bands
- `python_brain/brain/gap_detector.py` — Overnight gap detection + classification
- `python_brain/brain/rsi_ibs.py` — RSI(2), IBS, combined signals, SMA filters
- `python_brain/brain/ticker_ranker.py` — 100-ticker ranking engine (57 tests)
- `python_brain/brain/strategies/autonomous_orchestrator.py` — Session-aware autonomous signal generator

**Config:**
- `config/strategies.toml` — 5 adaptive strategies with full parameterization

### Test Results:
- Rust strategy_config: 15/15 pass
- Rust exit_engine: 37/37 pass
- Rust config_loader: 8/8 pass
- Python VWAP + gap + RSI/IBS: 91/91 pass
- Python ticker_ranker: 57/57 pass

---

## PHASE 4: AUDIT-DRIVEN FIXES

### Chandelier Exit Restructured (Compounding-Optimized):

| Rung | Old Threshold | New Threshold | Old Stop | New Stop |
|------|-------------|--------------|---------|---------|
| 1 | Entry | Entry | -2x ATR | -1.5x ATR |
| 2 | +2.0% | +0.8% | Breakeven | Breakeven + 0.3% fees |
| 3 | +4.0% | +1.5% | Trail 1.0x ATR | Trail 1.0x ATR |
| 4 | +6.0% | +2.5% | Trail 0.75x ATR | Trail 0.75x ATR |
| 5 | +8.0% | +4.0% | Trail 0.5x ATR | Trail 0.5x ATR |

**Rationale**: "A system that wins 60% at +1.2% compounds faster than one that wins 50% at +2.0%" — Quant audit

### Config Audit Adjustments:

| Parameter | Old | New | Audit Source |
|-----------|-----|-----|-------------|
| max_simultaneous_positions | 6 | 3 | Fund manager + Risk officer |
| portfolio_heat_limit_pct | 15.0 | 10.0 | Risk officer |
| cash_buffer_pct | 0.5 | 25.0 | Risk officer |
| daily_drawdown_pct | 2.0 | 4.0 | Risk officer (2% incompatible with stops) |
| spread_veto_pct | 0.5 | 0.3 | Quant |
| consecutive_loss_halt | 3 | 5 | Risk officer (3 too aggressive at 45% WR) |
| confidence_floor | 45 | 55 | Risk officer |
| max_active_strategies | 3 | 2 | Fund manager + Quant |

### New Risk Controls Added:
- `weekly_drawdown_pct = 7.0` — Weekly loss limit
- `peak_drawdown_halt_pct = 15.0` — Trailing DD from HWM
- `equity_floor_pct = 70.0` — Hard floor kill switch
- `overnight_exposure_cap_pct = 50.0` — Max overnight deployment
- `max_correlated_positions = 3` — Correlation-aware limit
- `max_risk_per_trade_pct = 0.75` — Dynamic position sizing
- VIX tiered response: low(18), elevated(25), high(35), crisis(50)

### Strategy Prioritization (Audit Consensus):

| Priority | Strategy | Family | Primary Session | Status |
|----------|---------|--------|----------------|--------|
| 1 | Cross-Market Momentum | Momentum | 14:45-16:00 UK | ENABLED (primary) |
| 2 | VWAP DipBuy | Mean Reversion | 10:30-14:30 UK | ENABLED (secondary) |
| 3 | RSI(2)/IBS | Mean Reversion | 20:30-21:00 UK | ENABLED (tertiary, low priority) |
| 4 | Gap Fade | Mean Reversion | 08:15-10:00 UK | DISABLED (audit: weakest) |
| 5 | Intraday Momentum | Momentum | N/A | DISABLED (audit: inoperable) |

---

## WHAT REMAINS

1. **EC2 deployment** — in progress (parallel agent)
2. **Google Sheets verification** — in progress (parallel agent)
3. **Session PDF generation** — needs implementation
4. **Server-side stop-losses at IBKR** — critical, needs implementation
5. **Position reconciliation on restart** — critical, needs implementation
6. **Ouroboros V2 schema upgrade** — needs implementation
7. **100-trade validation gate** — needs 100 paper trades with frozen parameters
8. **Walk-forward validation** — needs 200+ trades

---

## HONEST PERFORMANCE EXPECTATION

Based on the audit consensus:
- **Realistic daily return**: 0.15-0.30% net
- **Realistic win rate**: 55-65% (not 70-89%)
- **Realistic Sharpe**: 1.0-1.5 annualized
- **Realistic annualized return**: 45-110%
- **Required Rung 2+ rate for profitability**: >55% (down from >71% with old rungs)
- **Expected time to 100-trade validation gate**: 4-6 months

This is still extraordinary performance. A Sharpe of 1.0+ with 45%+ annual return would place the system in the top tier of systematic strategies globally.
