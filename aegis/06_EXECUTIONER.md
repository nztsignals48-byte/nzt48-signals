# AEGIS — The Executioner: Execution Pipeline + ML
> Extracted from AEGIS Master Plan v16.2.
> See [README](README.md) for full index.
---

# SECTION 4: THE EXECUTIONER {#section-4}

## Current Execution Flow (Verified)

1. Signal passes 33-gate gauntlet
2. ISA eligibility gate (THREE-KEY SAFE: Regulatory + Broker + Venue)
3. DynamicSizer computes position size (regime-conditional Kelly)
4. ExecutionPlanner: cost-aware plan + spread gate + net R:R
5. VirtualTrader opens position
6. VT inline profit ladder manages exits (6-rung canonical ladder)

## Execution Upgrades

### U-01: Bayesian Stranger Penalty (Replace Static 0.5x)

```
kappa(n, DSR) = kappa_min + (kappa_max - kappa_min) x f_DSR(DSR) x f_n(n)

Where:
  kappa_min = 0.25    (floor: quarter-Kelly for strangers)
  kappa_max = 1.0     (full Kelly for graduated tickers)
  lambda = 0.5        (DSR sensitivity — was 0.8 in v12, conservative)
  DSR_min = 1.5       (minimum DSR to consider)
  n0 = 50             (prior pseudo-count — was 30 in v12, stricter)
```

### U-02: Stoikov EV Gate (OBI-Adjusted Entry Price) — SHADOW MODE LOCKED

**STATUS: SHADOW MODE LOCKED until Phase Q2.** `ibkr_source.py` provides L1 quotes only (best bid/ask). OBI computation requires L2 order book depth data via `ib.reqMktDepth()`, which is NOT implemented. U-02 MUST NOT influence entry decisions until L2 data pipeline is live. During Phase Q1, U-02 runs in shadow mode: compute and LOG the Stoikov adjustment for every signal, but do NOT apply it to entry price or R:R veto. This creates a validation dataset for Phase Q2 calibration.

```
s_L = s_mid + L x beta_OBI x OBI x sigma_1min x ln(T / (T - t))

Where:
  beta_OBI = 0.5 x L^1.2  (continuous, not discrete tiers)
  VETO if net R:R < 1.5:1 after slippage

Note: R-12 OBI gate requires L2 data. SHADOW MODE ONLY until Phase Q2.
LSE Closing Auction Bypass: Stoikov disabled at 16:20 UK.
L2 dependency: ib.reqMktDepth() (IBKR Market Depth subscription required, ~$10/mo for LSE).
```

### U-03: Profit Ladder (CANONICAL — VT Inline 6-Rung)

The VT inline ETP ladder is the SINGLE SOURCE OF TRUTH. ChandelierExit.register() is dead code (never called). Must either wire it or formally remove it.

**Canonical Ladder (from virtual_trader.py)**:

| Rung | ETP Return | Action |
|------|-----------|--------|
| 1 | +1% | Move stop to breakeven |
| 2 | +2% | Sell 25%, trail remainder |
| 3 | +4% | Sell 25%, trail remainder |
| 4 | +6% | Sell 25%, trail remainder |
| 5 | +8% | Runner — trail with tight stop |
| 6 | +10%+ | Extended runner — maximum trail |

**Corrected Kelly with this ladder**: Blended average win ~+5.0%, avg loss -3.0%, Kelly f* = 0.280 at 55% WR.

### U-04: Dynamic Heat Cap Per Ticker

```
max_heat(ticker) = min(0.03 x ADV_20d, equity_heat_cap)
```

---

# SECTION 5: THE OUROBOROS — ML {#section-5}

## Current ML State (Verified)

- LightGBM + XGBoost ensemble (55/45 blend)
- 14 features, binary meta-label (De Prado 2018)
- 413+ trades logged, weekly retrain OR 50 new trades
- SHAP stability filter active

## Required ML Fixes

**M-01: Remove Feature Leakage (P0)**
- Remove `confidence` from feature_cols
- Add `raw_indicator_count`, `spread_bps`, `time_since_regime_change_hours`

**M-02: Fix Regime Map (P0)**
- `_REGIME_MAP` doesn't match actual `RegimeState` enum — always encodes -1
- Align with actual regime strings

**M-03: Fix should_retrain() (P0)**
- `should_retrain(self, last_trained_at: datetime)` at ml_meta_model.py:537 requires positional arg. Caller at main.py:5605 calls `self.ml_model.should_retrain()` with ZERO args → `TypeError`, silently caught by surrounding `try/except`. ML NEVER auto-retrains via main.py.
- Fix: remove `last_trained_at` parameter from method signature, use `self._last_trained_at` internally instead

**M-04: Walk-Forward Validation**
- FIX TARGET: `core/ml_meta_model.py:287-288` (NOT learning_engine.py — that is the wrong file)
- Replace `StratifiedKFold(n_splits=5, shuffle=True)` with expanding-window walk-forward with 5-day purge/embargo
- `shuffle=True` on time-series data is worst-case temporal leakage

**M-05: ML Bypass During Paper**
- N < 500: Pure LogReg fallback (5 PCA features)
- N < 200: ML DISABLED entirely. Frequency baseline only.

**M-06: Entry Timing Feedback Loop (NEW — v15.3)**
- For every executed trade, compute Entry Timing Score: `(daily_high - entry) / (daily_high - daily_low)` for LONG. For SHORT/inverse: `(entry - daily_low) / (daily_high - daily_low)`. Target: < 0.50 (entered in first half of move).
- **QUANTITATIVE PROOF GATE (Gemini Q10)**: After 100 trades post-timing-fixes, compute the MEDIAN Entry Timing Score across all trades. If median >= 0.50, the timing fixes have NOT worked — the system is still buying tops. This is the mathematical proof that T-01 through T-08 achieved their objective. Gate criteria: `median(ETS_100_trades) < 0.50`. If gate fails: HALT further infrastructure work and re-examine timing logic. This gate should be evaluated alongside RK-01 (100-Trade Validation Gate, WR >= 40%). Both gates must pass: WR >= 40% AND median ETS < 0.50.
- Log Missed Alpha: for every ticker moving > 2% that was NOT traded, record which gate rejected it and the ticker's EOD performance.
- Weekly Gate Rejection Audit: if a gate rejects > 30% of signals AND > 50% of rejected signals would have been profitable, flag for threshold adjustment.
- After 200+ trades: train Optimal Entry Delay model (gap_size, rvol_trajectory, sector_momentum, regime, time_of_day -> optimal_delay_minutes).
- Stop/target recalibration: once early entries are achieved, the MAE/MFE distribution will shift. Recompute stop_pct and target_pct from fresh data (current suggested: stop=0.3%, target=0.5% — these are calibrated on LATE entries and will change).

---

# SECTION 5B: CONSTITUTIONAL BOUNDS ON ML {#section-5b}

The learning engine may adjust parameters within bounds. It may NEVER touch:
- Position limits (max 4 concurrent)
- Stop-loss levels (0.75% risk cap)
- Leverage settings
- Circuit breaker thresholds

**Parameter drift limit**: +/-15% from baseline (aligned to Constitutional R23). If any parameter drifts beyond this, system enters DEFENSIVE mode: reduced sizing, P1 alert, mandatory review.

**Minimum trade count**: 100 trades before ANY parameter adjustment. 500 before full ML ensemble active.

---
