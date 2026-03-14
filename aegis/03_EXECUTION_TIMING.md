# AEGIS — Execution Timing Architecture
> THE #1 PRIORITY. Late-entry root cause fix.
> Extracted from AEGIS Master Plan v16.2.
> See [README](README.md) for full index.
---

# SECTION 2B: EXECUTION TIMING ARCHITECTURE — THE #1 PRIORITY {#section-2b}

## The Problem

The system identifies the right tickers but enters trades 15-60 minutes too late. By that point, the 2-6% intraday move on leveraged ETPs is 80-95% complete, and mean reversion pushes price back into the stop. Result: 0/52 S15 wins.

**Evidence from logs**: Scan cycles fire at fixed cron times (06:00, 08:00, 12:00, 14:30, 16:00, 19:00, 20:30 UK). Between these windows, a 4% move can start and complete without any scan running. When a scan does fire, 11 sequential filters delay the signal by another 5-30 minutes before an order can be placed.

## The 11 Timing Defects (In Priority Order)

### T-01: REMOVE First-30-Minute Blackout [P0-CRITICAL]

**Current** (`daily_target.py:324-333`): Hard veto on all signals before 09:30 UK.

**Problem**: Leveraged ETPs gap 3-6% at LSE open on overnight US/Asia news. The opening 10-15 minutes is when 50%+ of daily momentum forms. Admati & Pfleiderer (1988) studied price discovery in normal markets — it does NOT apply to gap repricing on 3x/5x leveraged products.

**Fix**: Replace hard blackout with OBSERVE-THEN-ACT protocol:
- 09:00-09:05 UK: OBSERVE only. Record LSE ETP opening prices for gap calculation.
- 09:05-09:15 UK: GAP SCAN. If any ETP gaps > 1.5% from previous close, fire immediate signal. Gap direction = signal direction for LONG ETPs; for INVERSE ETPs (QQQS.L, 3USS.L), gap direction is INVERTED from underlying. No indicator consensus required — the gap IS the signal. **CRITICAL GUARD (RO-01 coupling)**: Gap signals during 09:00-09:10 UK MUST pass the RO-01 toxic spread hard-cap (35 bps). If bid-ask spread > 35 bps at signal time, the gap signal is BLOCKED even if gap > 1.5%. This prevents buying into wide opening spreads where the 2% gap is consumed by execution cost. RO-01 has supremacy over T-01 gap scan.
- 09:15+ UK: Normal scanning resumes with full indicator gates.
- Academic basis: Gao et al. (2018) — first-30-min return predicts EOD direction with 62% accuracy for leveraged products.

**IMPLEMENTATION DETAILS (missing from v15.3)**:
1. **Data source for gap calc**: `_record_intraday_opens` (main.py:5955-5979) currently fetches US underlying prices (QQQ, NVDA, TSLA) via yfinance, NOT LSE ETP opening prices. With IBKR now primary, use `IBKRSource.fetch_bars(ticker, period='1d', interval='1m')` to get 09:00 UK open candle for LSE .L tickers. Falls back to `yf.download(tickers=isa_tickers, period='1d', interval='1m')` if IBKR unavailable.
2. **IndicatorSnapshot gap fields**: Add `prev_close: float` and `open_price: float` fields to IndicatorSnapshot in `models.py` (currently has neither). Gap = `(open_price - prev_close) / prev_close`.
3. **Gap signal construction**: Fire a `Signal` object with: `confidence=70` (between 65 floor and 75 old floor), `direction=1` if gap > +1.5% (LONG ETP) or gap < -1.5% (SHORT/inverse ETP), `stop_distance = 1.5 * ATR(14)` (ATR-based, standard), `target_distance = gap_size * 0.5` (capture half the gap). Note: `_MAX_GAP_UP_PCT = 0.015` (daily_target.py:211) currently SKIPS gaps >1.5% — this constant must be REMOVED or repurposed as the gap signal TRIGGER threshold.
4. **Gap database**: Store in Redis: `nzt:gap:{date}:{ticker}` → `{gap_pct, open_price, prev_close, direction, signal_fired}` with TTL 48h. Consumed by ML learning loop (Section 5 feedback).
5. **Code change**: Replace lines 324-333 of daily_target.py (the 30-min blackout) with the observe-then-act branching logic.

### T-02: REMOVE Lunch Dead Zone Blackout [P0-CRITICAL]

**Current** (`daily_target.py:335-344`): Hard veto on all signals 11:30-13:00 UK.

**Problem**: 11:30-13:00 UK = 06:30-08:00 ET = US pre-market. LSE leveraged ETPs that track US underlyings (QQQ3.L, NVD3.L, TSM3.L) reprice during this window on US overnight/pre-market moves. Blocking signals here means the system misses the entire US pre-market repricing cycle.

**Fix**: Replace hard blackout (daily_target.py:335-344) with REDUCED-CONFIDENCE window:
- 11:30-13:00 UK: Allow signals but apply -10 confidence penalty (not a veto).
- RVOL gate during this window: lower to 0.50 (from 0.85) because low UK volume is expected — the move is driven by US futures, not UK order flow.
- If underlying US ticker (from PEER tier) shows > 2% pre-market move, BOOST confidence by +15 instead of penalising.

**IMPLEMENTATION DETAILS (missing from v15.3)**:
1. **Confidence penalty ordering**: The -10 penalty applies AFTER the base confidence is computed by `_score_ticker_with_reason()` but BEFORE the `_MIN_CONFIDENCE` gate check. This means a signal at base 74 → 64 after penalty → REJECTED by 65 floor. This is intentional — lunch window signals need higher base conviction.
2. **US pre-market data source**: `_record_intraday_opens` (main.py:5963-5967) has the PEER tier mapping (QQQ→QQQ3.L, etc.) but this data is NOT accessible from `DailyTargetStrategy`. Must inject US pre-market data into the strategy via the `context` dict or a new `us_premarket` parameter. With IBKR, use `IBKRSource.fetch_bars(ticker, period='1d', interval='1m')` with `useRTH=False` for extended hours data. Falls back to `yf.download(ticker, prepost=True, period='1d', interval='1m')` with 15-min cache.
3. **Code change**: Replace lines 335-344 of daily_target.py with conditional confidence modifier.

### T-03: Switch to EVENT-DRIVEN Scanning [P0-CRITICAL]

**Current**: The `continuous_24_7` job (main.py:5274-5285) already runs ALL strategies including S15 every 60s. Additionally, scheduled deep scans fire at 06:00, 08:00, 12:00, 14:30, 16:00 UK. The 60s continuous scan IS connected to S15 — the bottleneck is S15's internal gates, not scan frequency.

**Problem**: Despite 60s scanning, S15's internal gates (30-min blackout, lunch dead zone, _daily_signal_fired, high ADX/RVOL) reject signals even when moves are detected. With T-01/T-02/T-06/T-07/T-08 fixing those gates, the remaining value of T-03 is adding anomaly-based PRIORITY scanning.

**Fix**: The 60s continuous scan already EXISTS and already runs S15 every 60s. The `continuous_24_7` job (main.py:5274-5285) invokes ALL strategies including S15 on every cycle. The bottleneck is NOT scan frequency — it is S15's internal gates (blackouts at :324-333 and :335-344, _daily_signal_fired at :348, high ADX/RVOL thresholds). T-01, T-02, T-06, T-07, T-08 fix those gates.

**REDEFINE T-03 AS**: Add a lightweight price-anomaly pre-filter to the existing 60s scan:
1. **Price-only heartbeat** (new function, ~30 LOC): On each 60s cycle, BEFORE running full S15 scan, check if any CORE ticker moved >1.0% from session open or >0.5% in last 5 minutes.
2. **Anomaly ticker priority**: If anomaly detected, run S15 scoring for ONLY those tickers first (skip non-anomaly tickers in that cycle). This reduces per-cycle latency from ~24s (12 tickers) to ~2s (1-2 anomaly tickers).
3. **session_open_price**: Must ADD `session_open_price` field to IndicatorSnapshot in `models.py` (does not exist). Populated from T-01's opening price recording. Alternatively, store in Redis: `nzt:session_open:{ticker}` at 09:00 UK.
4. **Code location**: Add the anomaly pre-filter to the top of `_run_continuous_scan()` or equivalent function that calls S15.

### T-04: Move GPD Tail Risk Pre-Screen to Nightly Batch [P0]

**Current** (`daily_target.py:414-435`): Downloads 270 days of history PER CANDIDATE during the scan loop. With IBKR primary: `IBKRSource.fetch_bars(ticker, period='1y', interval='1d')` — faster than yfinance (no scraper overhead), but still 12 sequential API calls. For 12 candidates at ~0.5s each = 6 seconds (vs 24s with yfinance). Still should be batched nightly per T-04 fix.

**Problem**: This is a STATIC computation (tail risk doesn't change intraday). Running it inline during the scan loop adds massive latency to every signal evaluation.

**Fix**:
- Compute GPD tail risk for all CORE tickers in the nightly intelligence cycle (`_run_nightly_intelligence` at main.py:7302, runs at 21:30 UK).
- Store results in Redis: `nzt:gpd:{ticker}` → `{"veto": false, "reason": "tail_ok", "tail_index": 0.03}` with TTL 24h. NOTE: `TailRiskMonitor.veto_signal()` returns `(bool, str)` — a boolean veto plus reason string, NOT a numeric tail_risk value. The Redis value format must match this API.
- During scan loop, READ from Redis (sub-millisecond) instead of computing inline.
- If Redis key missing for a ticker, use conservative default: `{"veto": false, "reason": "default_conservative"}` — NOT a numeric `tail_risk = 0.05`.

**IMPLEMENTATION DETAILS (missing from v15.3)**:
1. **Redis client injection**: `DailyTargetStrategy.__init__()` (daily_target.py:295) accepts `spread_tracker` but has NO `redis_client` parameter. Must add `redis_client=None` to `__init__()` and pass it from main.py when constructing S15.
2. **Code change**: Replace GPD inline computation at daily_target.py:414-435 with `redis_client.get(f"nzt:gpd:{ticker}")` lookup.
3. **Nightly batch**: Add GPD computation for all CORE tickers to `_run_nightly_intelligence()` at main.py:7302. Use existing `TailRiskMonitor` class, call `veto_signal()` per ticker, store result.
4. **VIX INTRADAY SUPREMACY OVER GPD CACHE (Gemini Q3)**: The nightly GPD cache is stale during a black swan event at 11:00 UK. Fix: The VIX circuit breaker (currently R-13 stop-widening context, but more precisely the SHOCK detection in `regime_classifier.py`) must have SUPREMACY over the GPD nightly cache. Implementation: if VIX spikes > 10 points intraday (delta from session open, NOT absolute level), ALL `nzt:gpd:*` Redis keys must be instantly invalidated (`redis_client.delete(*gpd_keys)`) and GPD must be recalculated inline for any ticker with an open position or pending signal. This is the ONE exception to the "no inline GPD" rule — a 10-point VIX spike is a regime change that invalidates last night's tail risk assessment. Code location: add VIX delta check to `_check_regime()` in main.py (or wherever VIX is polled each 60s cycle). If `vix_current - vix_session_open > 10`: (a) invalidate GPD cache, (b) recompute GPD for open positions only (not full universe), (c) log `GPD_CACHE_INVALIDATED_VIX_SPIKE`.

### T-05: Reweight Indicator Consensus for Fast-Move Detection [P0]

**Current** (`daily_target.py:127-202`): 8 indicators with flat/near-flat weighting. EMA20, EMA50, and OBV are lagging indicators that can't confirm a move until it's 40-60% complete.

**Problem**: In a fast 2-4% gap move, only VWAP + MACD + RSI align immediately (3/8). The system requires 6/8 consensus = REJECTED. By the time EMA20/50 catch up, the target is hit.

**Fix**: Two-tier indicator architecture:
- **FAST TIER** (for gap/momentum moves): VWAP, MACD histogram (use `snap.macd_histogram` at models.py:179 — NOT macd_line or macd_signal), RSI, Rate-of-Change (ROC 5-period). Weight 2.0x each. If 3/4 FAST indicators agree AND the move is > 1.5% from previous close, fire signal without waiting for SLOW tier.
- **SLOW TIER** (for continuation/trend moves): EMA9, EMA20, EMA50, OBV, Stochastic RSI. Weight 1.0x each. These add to confidence score but are NOT required for fast-move signals.
- **Combined consensus**: FAST tier = pass if >= 3/4. SLOW tier = adds 0-5 confidence points per agreeing indicator. Total confidence must still pass 65 floor (after SK-03 alignment).
- This lets the system fire on fast moves immediately while still using lagging indicators for marginal trades.

**IMPLEMENTATION DETAILS (missing from v15.3)**:
1. **ROC field missing**: `IndicatorSnapshot` (models.py:155-234) has NO `roc` or `rate_of_change` field. Must ADD `roc_5: Optional[float] = None` to the dataclass. Compute ROC as `(close - close_5_periods_ago) / close_5_periods_ago * 100` using 5-minute candles from IBKR (`IBKRSource.fetch_bars(ticker, period='1d', interval='5m')`), falling back to yfinance. Add computation to whichever module populates IndicatorSnapshot (likely `feeds/` layer).
2. **_determine_direction() return signature**: Currently returns `(direction, momentum_score, weighted_score)` at daily_target.py ~line 790. Splitting into FAST/SLOW tiers requires changing this to `(direction, momentum_score, weighted_score, tier: str)` where tier is "FAST" or "SLOW". All call sites of `_determine_direction()` must be updated.
3. **FAST/SLOW tier determines gate thresholds**: T-05 MUST be implemented BEFORE T-06 (ADX) and T-07 (RVOL) because the tier classification determines which ADX/RVOL thresholds to apply. The plan's implementation order (Section 2I.6) puts T-06 before T-05 — this is a dependency violation. Correct order: T-08, T-01, T-02, T-04, **T-05**, T-06, T-07, T-03, T-10.
4. **Gate ordering chicken-and-egg**: Current ADX gate at daily_target.py:542-544 runs BEFORE `_determine_direction()` at :598 which would determine FAST vs SLOW tier. Resolution: apply the LOWEST threshold (ADX >= 15) universally as a pre-filter, then after tier classification, apply the tier-specific threshold as a secondary check.

### T-06: Lower ADX Requirement [P0]

**Current** (`daily_target.py:77-79`): ADX >= 25 hard gate.

**Problem**: ADX is a 14-period lagging indicator. At the START of a breakout move, ADX = 10-18 (prior consolidation). ADX only reaches 25 after the move is 40-60% done. Wilder (1978) defined 25 as "strong trend" — but we need to catch the trend at birth, not confirmation.

**Fix**:
- ADX >= 15 for FAST tier moves (gap/momentum). This catches trend initiation.
- ADX >= 20 for SLOW tier moves (continuation). Still below the original 25.
- ADX >= 25 for RANGE_BOUND regime only (higher bar in choppy conditions).
- Add ADX DELTA check: if ADX is rising > 2 points per bar (acceleration), treat current ADX as +5 (predictive adjustment). A rising ADX at 18 behaves like ADX 23 — it's confirming.

**IMPLEMENTATION DETAILS (missing from v15.3)**:
1. **Gate ordering**: Current ADX gate at daily_target.py:542-544 fires BEFORE tier classification (T-05). Resolution: apply universal pre-filter ADX >= 15 at line 542, then apply tier-specific check AFTER `_determine_direction()` returns the tier. This requires restructuring the gate order in `scan()`.
2. **ADX delta field**: IndicatorSnapshot (models.py) has `adx14` (line 190) but NO `adx_delta` or `adx_prev`. Must ADD `adx_delta: Optional[float] = None` to IndicatorSnapshot. Compute as `adx_current - adx_previous_bar` using 5-minute candle data (same timeframe as RSI/MACD). Store previous bar's ADX in the indicator pipeline.
3. **"Per bar" timeframe**: Use 5-minute candles (consistent with other indicators). ADX delta = `adx14_current_5m - adx14_previous_5m`. A rise > 2.0 points over one 5-minute bar indicates strong acceleration.

### T-07: Lower RVOL Thresholds [P1]

**Current** (`daily_target.py:66,130,232`): MIN_RVOL = 0.85, eased to 1.0, range-bound requires 1.5.

**Problem**: Gap moves on earnings/news happen on LOW initial RVOL because the repricing is driven by off-exchange blocks and overnight positioning. By the time RVOL rises to 0.85+ (institutional participation), the gap has been absorbed.

**Fix**:
- FAST tier (gap moves): MIN_RVOL = 0.30 (just needs non-zero volume). The gap itself is the signal — volume follows.
- SLOW tier (trend continuation): MIN_RVOL = 0.65 (institutional participation building but not yet confirmed).
- RANGE_BOUND regime: MIN_RVOL = 1.2 (was 1.5 — still high conviction but not 95th percentile).
- Late-day trough (13:30-14:30): MIN_RVOL = 0.80 (was 1.5 — completely unreasonable for low-volume window).
- `_CONSENSUS_EASED_MIN_RVOL = 1.0` (daily_target.py:130) must also change to match new SLOW tier threshold (0.65) or be removed.
- Add RVOL TRAJECTORY: If RVOL is rising (current > 2x average of last 3 bars), treat as "volume confirming" regardless of absolute level.

**IMPLEMENTATION DETAILS (missing from v15.3)**:
1. **Gate ordering**: Same chicken-and-egg as T-06. Current RVOL gate at daily_target.py:538 runs BEFORE tier classification. Resolution: apply universal pre-filter RVOL >= 0.30 at line 538, then apply tier-specific check AFTER tier is known.
2. **RVOL trajectory field**: IndicatorSnapshot has a single scalar `rvol` field (models.py:193). NO rolling history. Must ADD `rvol_history: Optional[list[float]] = None` (last 3 bars of 5-minute RVOL) OR compute trajectory inline: `rvol_rising = current_rvol > 2.0 * mean(rvol_prev_3)`. Store the 3-bar rolling window in the indicator pipeline using 5-minute candles.
3. **"Last 3 bars" = last 3 five-minute candles** (15 minutes of RVOL history). Use 5-minute candle volume / 20-day average 5-minute volume for each bar.

### T-08: Remove `_daily_signal_fired` Single-Fire Limit [P0]

**Current** (`daily_target.py:348,497`): `self._daily_signal_fired[today] = True` — after the first S15 signal fires, ALL subsequent signals for the day are blocked.

**Problem**: This is old V1 code from when S15 was designed for "one best trade per day". The plan explicitly says to remove it (E-01, Section 2). It's still in the code.

**Fix**: Delete `_daily_signal_fired` entirely AND update `_MAX_SIGNALS_PER_DAY`. Specific changes:
1. Remove `self._daily_signal_fired: dict[str, bool] = {}` at daily_target.py:297
2. Remove the check block at daily_target.py:346-350 (`if self._daily_signal_fired.get(today, False): return []`)
3. Remove the set+cleanup at daily_target.py:497-502 (`self._daily_signal_fired[today] = True` and old_dates cleanup)
4. Change `_MAX_SIGNALS_PER_DAY = 1` (daily_target.py:70) to `_MAX_SIGNALS_PER_DAY = 4` to match portfolio governor. OR remove the constant entirely if portfolio governors are the sole limiters.
5. **S15 "max 1 position" gate**: `_execute_s15_priority_path` (main.py:3997-4004) enforces "Max 1 S15 position at a time" — this is a SEPARATE constraint from _daily_signal_fired. After removing _daily_signal_fired, signals can fire multiple times per day but only 1 S15 position open at once. DECISION NEEDED: should this gate be raised to allow 2-3 concurrent S15 positions? Recommend keeping at 1 for Phase Q1 (conservative).
6. **MUST be deployed simultaneously with SK-04 fix** (SessionProtection +1.5% removal).

### T-09: Add Pre-Market Intelligence Scan [P1]

**Current**: Pre-market scan fires at 06:00 UK but only runs S1/S3/S5/S6/S7/S10. S15 has no pre-market capability.

**Problem**: US after-hours/overnight moves (earnings, macro data) are known by 06:00 UK but S15 doesn't process them until 08:00+ UK open scan. By then the ETP has already gapped.

**Fix**: New pre-market intelligence module:
- 07:30 UK: Fetch US overnight futures via IBKR (`reqHistoricalData` for NQ, ES, SOX futures), falling back to yfinance (^NQ, ^ES, ^SOX).
- If any sector index moved > 1.5% overnight, flag corresponding CORE ETPs as GAP_CANDIDATES.
- 08:00 UK (LSE open): GAP_CANDIDATES get PRIORITY scanning — S15 evaluates them first, with FAST tier gates only.
- Store overnight moves in Redis: `nzt:premarket:{date}:{ticker}` for ML learning loop.

### T-10: Optimise Qualification Gauntlet Latency [P1]

**Current**: S15 already BYPASSES the 18-gate gauntlet via `_execute_s15_priority_path` (main.py:3746-4081), extracted at line 1944. The priority path has ~17 hard gates (discipline, earnings fade, PEAD, VWAP, sector momentum, expiry pinning, window dressing, gap analytics, LSE hours, daily loss limit, max 1 position, VIX, IV crush, net expectancy, capacity, tail loss, position sizing) plus ~14 confidence modifiers. The docstring at main.py:3754-3765 claims "5 ESSENTIAL GATES" but actual code has ~17 — docstring is stale.

**Problem**: Even the priority path has ~17 gates with database queries. Needs to be reduced for FAST tier signals.

**Fix** (REWRITTEN to match code reality):
- **FAST tier signals**: Reduce S15 priority path from ~17 gates to 7 essential gates: (1) circuit breaker check, (2) ISA eligibility, (3) portfolio heat cap, (4) correlation brake, (5) position limit, (6) risk sizing, (7) cost drag check. REMOVE from FAST path: discipline gate, earnings fade, PEAD, sector momentum, expiry pinning, window dressing, gap analytics, IV crush, net expectancy model, tail loss scorer.
- **SLOW tier signals**: Keep all ~17 gates in priority path.
- Pre-compute portfolio state every 60s (position count, heat, correlations) and cache in Redis so gates read cached values instead of querying DB.
- Target: FAST path < 500ms, SLOW path < 3s.
- **ALSO FIX**: Update stale docstring at main.py:3754-3765 from "5 ESSENTIAL GATES" to reflect actual ~17-gate (or post-fix 7/17-gate) architecture.

### T-11: Add Predictive Entry Timing [P2]

**Current** (`daily_target.py:654`): Entry = `round(price, 2)` — just current market price.

**Problem**: No anticipation. The system reacts to moves instead of predicting them.

**Fix** (Phase 2+):
- Use the overnight gap + first-30-min return data (Gao et al. 2018) to predict likely EOD direction.
- If prediction confidence > 70%, pre-stage a limit order at the current price + 0.5% pullback. This gets a better entry if the move pulls back (common after gap moves).
- If no pullback within 15 minutes, convert to market order.
- ML model trains on: gap size, RVOL trajectory, sector momentum, time-of-day — predicts optimal entry delay (0, 5, 10, 15 minutes after signal).

## Execution Timing Summary: Before vs After

| Component | v15.2 (Current) | v15.3 (Fixed) | Latency Saved |
|-----------|-----------------|---------------|---------------|
| Opening blackout | 30-min hard veto | 5-min observe + gap scan | **25 min** |
| Lunch blackout | 90-min hard veto | Reduced confidence (not veto) | **90 min** |
| Scan frequency | Cron (2-4 hours gap) | 60s heartbeat + event trigger | **Up to 120 min** |
| GPD pre-screen | Inline yfinance (24s) / IBKR (6s) | Nightly batch + Redis read | **6-24s per cycle** |
| Indicator consensus | 6/8 with lagging | FAST tier 3/4 leading only | **5-10 min** |
| ADX threshold | >= 25 (mid-trend) | >= 15 FAST / 20 SLOW | **3-5 min** |
| RVOL threshold | >= 0.85 (confirmed) | >= 0.30 FAST / 0.65 SLOW | **5-15 min** |
| Single-fire limit | 1 signal/day | Removed (portfolio governs) | **Infinite** |
| Pre-market intel | None for S15 | 07:30 overnight scan | **30+ min** |
| Qualification | 18 gates, 4.5s | 7 gates FAST path, 500ms | **4.0s** |
| Entry type | Market at signal | Limit pullback with timeout | **Better fill** |

**Net effect**: A gap move that previously took 60+ minutes to detect and enter should now be detected within 60 seconds and entered within 5 minutes.

## ML Learning Loop for Trade Timing (Enhancement to Section 5)

The Ouroboros must learn from EVERY trade where it was too late. New feedback signals:

1. **Entry Timing Score**: For every executed trade, compute `(daily_high - entry_price) / (daily_high - daily_low)`. A score of 0.9 = entered near the top (too late). A score of 0.2 = entered near the bottom (early, good). Target: < 0.5 for LONG trades.

2. **Missed Alpha Log**: For every ticker that moved > 2% intraday but was NOT traded (filtered by gates), log: which gate rejected it, at what time, and what the ticker did for the rest of the day. This creates a training set of "correct rejections" vs "missed opportunities".

3. **Optimal Entry Time Model**: After 200+ trades, train a simple model: given (gap_size, rvol_trajectory, sector_momentum, regime, time_of_day), predict optimal_entry_delay_minutes. Use this to time entries more precisely.

4. **Stop Calibration from MAE/MFE**: The playbook already shows `suggested_stop_pct = 0.3%` and `suggested_target_pct = 0.5%`. These are based on LATE entries. Once early entries are achieved, recalibrate: the MAE should shrink (less adverse excursion when entering early) and MFE should grow (more favourable excursion when catching the start of the move).

5. **Gate Rejection Audit**: Weekly automated report: for each gate (ADX, RVOL, consensus, timing), compute: (a) how many signals it rejected, (b) of those rejected, how many would have been profitable. If a gate rejects > 30% of signals AND > 50% of rejected signals would have been profitable, flag the gate for threshold adjustment.

---

### Section 2C: Institutional Microstructure Defenses (20 Items) {#section-2c}

Full specification: `SECTION_2C_INSTITUTIONAL_MICROSTRUCTURE_DEFENSES.md`

**Summary**: 20 defenses across 4 institutional personas:
- **Chief Quant (CQ-01 to CQ-05)**: Vol-scaled stops, mid-price illusion filter, asymmetric leverage decay, reversal cooldown, cross-asset divergence veto
- **Lead Systems Architect (SA-01 to SA-05)**: SLOW indicator cache, JIT compilation, async telemetry, LOBCache, stale order cancellation
- **Chief Risk Officer (RO-01 to RO-05)**: Toxic spread cap, instant-stopout halt, underlying inventory limit, gap sizing penalty, maker-pegged limits
- **Academic Reviewer (AR-01 to AR-05)**: Kalman noise filter, signal half-life, walk-forward validation, regime-conditioned gates, survivorship bias policy

**Impact**: +5 P0 items (total: 23), +7 P1 items (total: 27), 16 new threshold table entries, 8 new Go-Live Gate criteria

---

### Section 2D: Apex-Level Architectural Modules (5 Modules, 9,008 lines) {#section-2d}

Production-grade implementation modules for the v15.4 execution overhaul:

| Module | File | Lines | Purpose |
|--------|------|-------|---------|
| Tachyon Acceleration Trigger | `strategies/tachyon_trigger.py` | 1,228 | Savitzky-Golay 2nd-derivative predictive entry (fires 4-10 bars before reactive indicators) |
| Lead-Lag Proxy Arbitrage | `core/lead_lag_arbitrage.py` | 1,629 | NQ futures -> QQQ -> LSE ETP price transmission exploitation |
| Ghost-Maker Dynamic Pegging | `execution/ghost_maker.py` | 1,861 | Bid+1tick limit order state machine with toxicity scoring |
| Micro-Regime Exhaustion Monitor | `core/exhaustion_monitor.py` | 1,325 | Hawkes self-exciting process + Volume-Time Decay for dynamic profit ladder |
| Disruptor Engine (Brain/Muscle) | `core/disruptor_engine.py` | 2,532 | LMAX-pattern async isolation: signal generation decoupled from execution |

**Mathematical Foundation**: `strategies/apex_timing_manifesto.py` (433 lines) -- Complete mathematical proof that 2nd-derivative detection front-runs TWAP/VWAP execution algorithms by 4-10 bars.

**Adverse Selection Analysis**: `ADVERSE_SELECTION_AUDIT.md` -- Pipeline-wide audit of information asymmetry exposure with quantified bps impact.

---
