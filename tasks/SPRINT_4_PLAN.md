# SPRINT 4 PLAN — Risk Hardening & Signal Quality
**Status**: READY FOR IMPLEMENTATION
**Prerequisites**: Sprints 0-3 complete + deployed (2026-03-07)
**Estimated effort**: 12-16 hours
**Goal**: Fix all CRITICAL/HIGH audit findings, harden risk controls, begin 100-Trade Validation Gate

---

## CONTEXT: What Sprints 0-3 Fixed

| Sprint | Key Changes | Status |
|--------|------------|--------|
| 0 | VIX fail-closed, confidence 75→65, 1→3 signals/day, data freshness gate | ✅ Deployed |
| 1 | Gap scan, FAST/SLOW tiers, lunch penalty, RVOL tiers, ADX tiers, anomaly priority | ✅ Deployed |
| 2 | Equity denominator fix, zombie halt 12h window | ✅ Deployed |
| 3 | **SA-8 SHOWSTOPPER** (continue bug killing all normal scans), CQ-3 gap regime check, SA-5 SheetsLogger equity refresh, RVOL trajectory logic fix, Power Hour confidence cap, chain boost daily reset, ADX acceleration gate, gap confidence scaling | ✅ Deployed |

**Critical state after Sprint 3**: Normal scan path is now LIVE for the first time. Monday will be the first session where both gap signals AND normal momentum signals can fire.

---

## SPRINT 4 ITEMS (Priority Order)

### T-11: VWAP Variance Correction (CRITICAL)
**File**: `feeds/indicators.py:315-355`
**Problem**: VWAP band variance uses cumulative `Σ(dev² × vol) / Σ(vol)` which grows unbounded over the session. By 10:30, bands are 2-3x wider than correct, causing false mean-reversion signals.
**Fix**: Replace cumulative variance with 20-bar rolling variance:
```python
# Before (line 337-338):
cum_dev_sq = (deviation ** 2 * df["Volume"]).cumsum()
variance = cum_dev_sq / cum_vol_safe

# After:
_VWAP_VARIANCE_WINDOW = 20
weighted_dev_sq = deviation ** 2 * df["Volume"]
rolling_sum_dev_sq = weighted_dev_sq.rolling(window=_VWAP_VARIANCE_WINDOW, min_periods=1).sum()
rolling_sum_vol = df["Volume"].rolling(window=_VWAP_VARIANCE_WINDOW, min_periods=1).sum()
variance = rolling_sum_dev_sq / rolling_sum_vol.replace(0, 1)
```
**Test**: Compare VWAP bands at 10:30 vs 15:30 — should be similar width (not 3x wider).
**Effort**: 1 hour

---

### T-12: Circuit Breaker Re-Check at Execution (CRITICAL)
**File**: `execution/virtual_trader.py` — `open_position()` method
**Problem**: Circuit breaker is checked in the main scan loop but NOT re-checked when `open_position()` is called. If P&L degrades between scan and execution (e.g., from a stop-out), the position opens despite breaker being in ORANGE/RED.
**Fix**: Add circuit breaker re-validation before entry:
```python
# In open_position(), after P&L kill switch check:
if hasattr(self, '_circuit_breakers') and self._circuit_breakers:
    _cb_result = self._circuit_breakers.check_all(
        daily_pnl=self._daily_pnl_pct,
        open_positions=list(self._positions.values()),
    )
    if not _cb_result.get("allow_new_entries", True):
        self.logger.warning("CIRCUIT_BREAKER_VETO at execution: %s", _cb_result.get("action", ""))
        return None
```
**Wire**: Pass circuit_breakers reference to VirtualTrader constructor.
**Effort**: 2 hours

---

### T-13: Correlated Position Monitor (HIGH)
**File**: `execution/virtual_trader.py` — new method `_check_correlated_losses()`
**Problem**: QQQ3.L + 3LUS.L are 95% correlated (both 3x Nasdaq). Two simultaneous losses = one correlated event counted as two, bypassing the 3-signal cap's intent.
**Fix**: Add underlying correlation map and check during position updates:
```python
_UNDERLYING_MAP = {
    "QQQ3.L": "NASDAQ", "3LUS.L": "NASDAQ", "QQQ5.L": "NASDAQ",
    "QQQS.L": "NASDAQ_INV", "3USS.L": "NASDAQ_INV",
    "SP5L.L": "SP500",
    "3SEM.L": "SEMICON", "NVD3.L": "NVIDIA", "TSL3.L": "TESLA",
    "TSM3.L": "TSMC", "MU2.L": "MICRON", "GPT3.L": "AI_BASKET",
}

def _check_correlated_entry(self, signal):
    """Block new entry if same underlying already has an open position."""
    new_ul = _UNDERLYING_MAP.get(signal.ticker, signal.ticker)
    for pos in self._positions.values():
        if pos.status == "OPEN":
            pos_ul = _UNDERLYING_MAP.get(pos.ticker, pos.ticker)
            if pos_ul == new_ul and pos.ticker != signal.ticker:
                self.logger.warning(
                    "CORRELATED_ENTRY_VETO: %s blocked — %s already open (same underlying: %s)",
                    signal.ticker, pos.ticker, new_ul,
                )
                return False
    return True
```
**Call site**: Before `open_position()` sizing.
**Effort**: 2 hours

---

### T-14: Chandelier Deactivation Logic (HIGH)
**File**: `core/chandelier_exit.py` — `update()` method
**Problem**: Once Chandelier activates at +2%, it locks the trailing stop. If price reverses to ≤+0.5%, the position is forced out near breakeven via whipsaw. No deactivation to revert to the original stop.
**Fix**: Add deactivation when position falls back below activation threshold:
```python
# After calculating pct_move, add:
if state.active and pct_move <= 0.5:
    state.active = False
    state.current_rung = 0
    # Revert to original 1.5× ATR stop (not breakeven)
    if state.direction == "LONG":
        state.trailing_stop = state.entry_price - (1.5 * state.atr_at_entry)
    else:
        state.trailing_stop = state.entry_price + (1.5 * state.atr_at_entry)
    logger.info("CHANDELIER_DEACTIVATE: %s reverting to original stop @ %.4f",
                state.ticker, state.trailing_stop)
```
**Risk**: If price recovers after deactivation, it must re-trigger at +2% to reactivate. This is correct — avoids whipsaw.
**Effort**: 1.5 hours

---

### T-15: ORANGE Level Circuit Breaker Size Multiplier Fix (MEDIUM)
**File**: `qualification/circuit_breakers.py:414-416`
**Problem**: `size_multiplier = 0.0` at ORANGE level (2.5-3.99% loss). Should be 0.50 per spec — 0.0 freezes all existing position management.
**Fix**: Change line 416: `size_multiplier = 0.0` → `size_multiplier = 0.50`
**Effort**: 5 minutes

---

### T-16: Qualification Loop Exception Safety (HIGH)
**File**: `main.py` — qualification loop (~line 2972)
**Problem**: If any gate throws an exception, the signal is silently dropped. No rejection record, no audit trail. Diagnosis impossible.
**Fix**: In the except handler, log the signal as SKIPPED with the exception as rejection reason:
```python
except Exception as e:
    logger.error("Qualification failed for %s %s: %s", signal.direction.value, signal.ticker, e)
    # Record the skip so it appears in diagnostics
    if hasattr(self, 'sheets') and self.sheets:
        try:
            self.sheets.log_signal(signal, status="SKIPPED", rejection_reason=f"EXCEPTION: {e}")
        except Exception:
            pass
```
**Effort**: 30 minutes

---

### T-17: Daily Reset State Completeness (MEDIUM)
**File**: `main.py` — `_run_daily_reset()`
**Problem**: Several stateful variables not cleared on daily reset:
- `_last_sector_top3` — sector rankings persist across days
- `_consecutive_losses` / `_last_stopout_time` — not refreshed from DB
**Fix**: Add to `_run_daily_reset()`:
```python
# Clear stale sector state
if hasattr(self, '_last_sector_top3'):
    del self._last_sector_top3

# Refresh loss counters from database
if hasattr(self, '_update_state_from_db'):
    try:
        self._update_state_from_db()
    except Exception as e:
        logger.error("Failed to refresh state from DB on daily reset: %s", e)
```
**Effort**: 30 minutes

---

### T-18: FAST Tier prev_close Diagnostic Logging (LOW)
**File**: `strategies/daily_target.py` — `_score_ticker_with_reason()` (~line 974)
**Problem**: If `prev_close=0`, FAST tier silently degrades to SLOW with no log.
**Fix**: Add diagnostic when prev_close blocks FAST:
```python
if prev_close <= 0:
    self.logger.debug("S15 FAST: %s prev_close unavailable — FAST tier disabled", ticker)
```
**Effort**: 5 minutes

---

## ITEMS DEFERRED TO SPRINT 5+

| Finding | Why Deferred |
|---------|-------------|
| VWAP variance (uses Bollinger as alternative) | T-11 fixes the math, but VWAP bands are used as a confidence modifier, not a gate — impact is incremental |
| HMM retraining frequency (weekly → daily) | Requires scheduler changes + validation; regime detection is a background signal, not a gate |
| HMM hysteresis (upper/lower thresholds) | Design decision — need data from 100-Trade Gate to calibrate thresholds |
| Cache TTL reduction (15min → 5min) | Need to verify yfinance rate limits; reducing TTL 3x increases API calls 3x |
| Kelly rounding auditor | Positions are 1-3 shares on £10K equity; rounding error is <£5 per trade |
| Stop gap-through model | Paper mode uses virtual fills at exact stop price; real-money mode will need this |

---

## VALIDATION GATE

After Sprint 4 deployment, the **100-Trade Validation Gate** begins:
1. System trades normally (paper mode) for minimum 100 trades
2. Track: Win Rate, Profit Factor, Average R, Max Drawdown, Sharpe
3. **Pass criteria**: WR ≥ 40%, Profit Factor ≥ 1.3, Max DD ≤ 5%
4. **If fail**: Diagnose via tier (FAST vs SLOW), time window, regime, and revise
5. Sprint 5 only proceeds if 100-Trade Gate passes

### Monitoring Checklist (Daily)
- [ ] Normal scan signals firing? (SA-8 fix validation)
- [ ] FAST vs SLOW signal ratio? (expect 30/70 split)
- [ ] Gap signals blocked in CRASH/SHOCK? (CQ-3 validation)
- [ ] RVOL trajectory no longer bypassing floor? (Finding 1 validation)
- [ ] Power Hour confidence ≤ 95? (Finding 2 validation)
- [ ] Chain boosts cleared at daily reset? (SD-1 validation)
- [ ] Circuit breaker re-check at execution? (T-12 validation, after impl)

---

## IMPLEMENTATION ORDER

```
Day 1 (4h):
  T-11 VWAP variance fix (1h)
  T-12 Circuit breaker re-check (2h)
  T-15 ORANGE level size fix (5min)
  T-18 FAST prev_close logging (5min)
  Deploy + verify

Day 2 (4h):
  T-13 Correlated position monitor (2h)
  T-14 Chandelier deactivation (1.5h)
  T-17 Daily reset completeness (30min)
  Deploy + verify

Day 3 (2h):
  T-16 Qualification exception safety (30min)
  Full system test (1.5h)
  Final deploy

Day 4+: Monitor 100-Trade Gate
```

---

## SUCCESS METRICS

| Metric | Before Sprint 4 | Target After |
|--------|-----------------|-------------|
| Normal scan signals per day | 0 (SA-8 bug) | 2-8 |
| Gap signals per day | 0-2 | 0-2 (unchanged) |
| RVOL false-pass rate | ~10% (trajectory bypass) | 0% |
| Power Hour confidence overflow | Possible (>95) | Never |
| Circuit breaker gaps | 1 (no re-check) | 0 |
| Correlated position limit | None | 1 per underlying |
| Chandelier whipsaw exits | ~30% of activated | <10% |
| Daily reset state leaks | 3 variables | 0 |
