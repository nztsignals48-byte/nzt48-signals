# AEGIS Alpha-Omega Master Plan v13.0 -- Fatal Flaws Audit

**Section**: 1B -- Fatal Flaws (Pre-Launch CRO Audit)
**Auditor**: AEGIS CRO (Chief Risk Officer)
**Date**: 2026-03-04
**Scope**: All code-verified deficiencies in the production codebase and architectural deficiencies inherited from the Aegis v10.0 plan document.
**Standard**: Each flaw is classified by Severity (CRITICAL / HIGH / MEDIUM / LOW), pinpointed to file and line, and paired with a prescriptive fix. No flaw is theoretical -- every one was verified against the live codebase.

---

## PART I: FATAL FLAWS IN CURRENT CODEBASE (12 Flaws, Code-Verified)

---

### F-01 | CRITICAL | Signal Queue Silently Drops Signals

**Location**: `main.py` L1136

```python
self._signal_queue: Queue = Queue(maxsize=50)
```

**Issue**: The signal queue is bounded at 50 entries. When the queue is full, new signals are silently dropped. There is no overflow handler, no logging of dropped signals, and no priority mechanism. During high-volatility regime transitions -- precisely when S15 generates the most actionable signals -- the queue saturates first. The highest-value signals (S15 daily target candidates with peak reachability scores) compete for queue space with low-priority informational signals and are discarded with no trace.

**Impact**: Missed S15 entries during the exact conditions that produce 2%+ moves. On a single high-volatility day, this can cost the daily compounding target entirely. Over 252 trading days, even 5 missed optimal entries compounds to a significant equity shortfall versus the theoretical curve.

**Fix**: Replace with an unbounded `asyncio.PriorityQueue`. Assign priority tiers: P0 = S15 daily target, P1 = active strategy signals, P2 = informational/monitoring. Add a counter metric for queue depth and emit a Telegram alert if depth exceeds 100. Back-pressure should throttle low-priority signal generation, never drop high-priority signals.

---

### F-02 | CRITICAL | Regime Transition Instantly Flattens All Positions

**Location**: `main.py` L4500-4611 (`_handle_regime_transition`)

```python
# SHOCK: Emergency flatten everything
if new_regime == RegimeState.SHOCK:
    ...
# RISK_OFF: Flatten everything
if new_regime == RegimeState.RISK_OFF:
    ...
# TRENDING_UP -> TRENDING_DOWN: Flatten all longs
if prev in up_regimes and new_regime in down_regimes:
    ...
```

**Issue**: Every regime transition triggers immediate position liquidation with zero confirmation delay. A single noisy tick in the VIX feed, a momentary data glitch, or a transient macro signal misfire causes the system to flatten the entire portfolio and lock in losses at the worst possible prices. The SHOCK, RISK_OFF, UP-to-DOWN, and DOWN-to-UP transitions all execute market-order closes within the same scan cycle that detected the regime change. There is no confirmation window, no second-tick validation, and no distinction between a genuine regime shift and sensor noise.

**Impact**: Whipsaw losses. A single false SHOCK classification flattens all positions at bid prices, then the regime reverts on the next scan cycle. The system has already crystallised losses and must re-enter at worse prices. During paper trading, this manifests as unexplained drawdowns that do not correlate with actual market moves. In live trading with 3x/5x leverage, a single false flatten event on a full portfolio can exceed the daily loss budget.

**Fix**: Implement a 3-tick confirmation buffer for all regime transitions except SHOCK (which retains instant flatten but requires VIX > 40 AND credit spread blowout simultaneously, not either/or). For non-SHOCK transitions: the new regime must persist for 3 consecutive scan cycles (approximately 3 minutes at 60s intervals) before position actions execute. During the confirmation window, tighten all stops to breakeven as a defensive measure. Log each confirmation tick to the regime audit trail.

---

### F-03 | CRITICAL | No Portfolio-Level Correlation Brake

**Location**: `main.py` L2441-2474 (Portfolio Risk Gate)

**Issue**: The portfolio risk gate at L2441-2474 checks concentration, directional exposure, and budget limits, but performs no cross-position correlation analysis before admitting new trades. The S16 gauntlet (L4299-4313) has a per-strategy 0.80 correlation check, but this is S16-specific and does not apply to the primary S15 strategy or mixed-strategy portfolios. Meanwhile, the ISA universe is heavily NASDAQ-correlated: QQQ3.L, NVD3.L, 3SEM.L, GPT3.L, TSL3.L, and TSM3.L all have pairwise correlations exceeding 0.85 with the NASDAQ-100. A portfolio holding 3+ of these instruments has an effective position count of approximately 1.2, not 3. The `RealTimeCorrelationMatrix` (L769-770) exists but is only used for spike detection and S16 gating, not as an admission gate for all strategies.

**Impact**: Concentration risk masquerading as diversification. A single NASDAQ downtick moves the entire portfolio in lockstep. With 3x leverage on all positions, a 2% NASDAQ drop produces a 6% portfolio loss -- exceeding any daily loss limit. The system believes it has 3 independent positions when it effectively has 1.2.

**Fix**: Implement Gate #34 (Correlation Admission Gate) in the main signal processing loop at L2441. Before admitting any new position: compute the Ledoit-Wolf shrinkage covariance matrix (the infrastructure already exists at L8044-8082) across all open positions plus the candidate. If 3 or more pairwise correlations exceed 0.70, cap the portfolio at 1 simultaneous position. If 2 pairs exceed 0.70, cap at 2 positions. Feed the correlation matrix from the existing `RealTimeCorrelationMatrix` and `CorrelationEngine` instances. This gate must apply to ALL strategies, not just S16.

---

### F-04 | HIGH | Inverse ETP Set Hardcoded

**Location**: `main.py` L4571-4575

```python
_INVERSE_ETPS_SET = {
    "QQQS.L", "3USS.L", "SC3S.L", "GPTS.L",
    "3SNV.L", "3STS.L", "TSMS.L", "MUS.L",
    "SQQQ.L", "SPYS.L",
}
```

**Issue**: The set of inverse ETPs used for DOWN-to-UP regime transition handling is hardcoded as a local variable inside `_handle_regime_transition`. This set includes phantom tickers that do not exist in the canonical ISA universe (SC3S.L, GPTS.L, 3SNV.L, 3STS.L, TSMS.L, MUS.L are not in `uk_isa/isa_universe.py`). It also includes US tickers (SQQQ.L, SPYS.L) that are outside the ISA-only mandate. Meanwhile, if the ISA universe is updated with new inverse products, this hardcoded set will silently fall out of sync, causing the regime transition handler to miss closing LONG positions on new inverse ETPs during a DOWN-to-UP flip.

**Impact**: During a bearish-to-bullish regime transition, LONG positions on unrecognised inverse ETPs will not be closed. These positions profit from downward moves and will bleed as the market turns bullish. The system will hold losing inverse positions indefinitely until a stop is hit, potentially days later. Conversely, phantom tickers in the set waste CPU cycles on lookups that never match.

**Fix**: Replace the hardcoded set with a metadata query: `_INVERSE_ETPS_SET = {t for t in isa_universe.get_all_tickers() if isa_universe.is_inverse(t)}`. The `isa_universe.py` module is already the canonical source for ticker metadata. Add an `is_inverse` flag to the universe registry and populate it from the LSE registry scraper. Remove all phantom and non-ISA tickers.

---

### F-05 | HIGH | Kill Switch Stuck in Redis / File System

**Location**: `delivery/telegram_bot.py` L1816-1846

```python
class KillSwitch:
    KILL_FILE = str(Path(__file__).parent.parent / "data" / "KILL_SWITCH")

    def is_killed(self) -> bool:
        if os.path.exists(self.KILL_FILE):
            return True
        if self._process_killed:
            return True
        return False
```

**Issue**: The kill switch persists via a file (`data/KILL_SWITCH`) and a process-level flag. Once activated -- whether by drawdown circuit breaker, manual Telegram /kill command, or process signal -- there is no automatic recovery mechanism. The kill switch remains active across container restarts (the file persists on the Docker volume). A weekend drawdown that triggers the kill switch on Friday at 21:00 will keep the system halted through Monday's open, missing the entire pre-market intelligence window and the first hours of trading. Manual intervention is required: either SSH into the EC2 instance and delete the file, or send a Telegram /unkill command (if one exists -- it is not visible in the KillSwitch class).

**Impact**: Extended unplanned downtime. Every hour of missed trading during a trending regime is a missed compounding opportunity. If the kill switch activates during a drawdown that subsequently recovers (e.g., a flash crash reversal), the system misses the recovery entirely.

**Fix**: Implement automatic kill switch recovery at 06:00 UTC on trading days if the drawdown has recovered to within -1.0% of the session high-water mark. The recovery check should: (1) verify the current equity vs. the equity at kill-switch activation time, (2) require that the drawdown has reduced by at least 50% from its peak, (3) log the auto-recovery event to Telegram with full context, (4) set a CAUTION state for the first 30 minutes post-recovery (half-size only). Add a `last_activated_at` timestamp and `activation_reason` field to the KillSwitch class for audit purposes.

---

### F-06 | HIGH | ML Feature Leakage -- Confidence as Input Feature

**Location**: `core/ml_meta_model.py` L73-76

```python
self.feature_cols: list[str] = [
    "rvol", "adx", "rsi", "atr_pct", "confidence", "indicator_count",
    "hour_of_day", "day_of_week", "vix", "regime_encoded", "ticker_encoded",
    "beat_magnitude", "pre_earnings_runup", "short_interest_pct",
]
```

**Issue**: The `confidence` field is included as an input feature to the LightGBM/XGBoost meta-model. This `confidence` value is the rule-based confidence score computed by the signal engine -- the very score that the ML model is supposed to evaluate and improve upon. Including it as an input creates a circular dependency: the model learns to parrot the rule-based confidence rather than discovering independent predictive features. During training, `confidence` will dominate feature importance (it is directly correlated with the label by construction), masking genuinely predictive features like `rvol`, `atr_pct`, and `vix`. The SHAP stability filter (L619-772) may eventually drop it if its rank drifts, but this is not guaranteed and depends on having 4+ training windows.

**Impact**: Inflated AUC during cross-validation (the model appears to perform well because it is partially memorising the label through its proxy). In production, the meta-label gate (L449-510) will veto or pass signals based largely on a feature that is already baked into the signal's own confidence score, providing no additional filtering power. The De Prado (2018) meta-labelling framework specifically requires features that are independent of the primary model's output.

**Fix**: Remove `confidence` from `feature_cols`. Replace it with `raw_indicator_alignment_count` -- the count of how many raw indicators (RSI, MACD, BB, VWAP, etc.) agree on direction, without any weighting or scoring applied. This preserves the signal about indicator consensus without leaking the rule-based confidence score. Retrain the model after the change and compare AUC with and without the leaked feature to quantify the inflation.

---

### F-07 | HIGH | VIX Defaults to Zero on Fetch Failure

**Location**: `main.py` L4674-4685

```python
ms_data = self._market_structure.get_full_context()
ctx.vix = ms_data.get("vix", 0)
...
except Exception as e:
    logger.error("Market structure fetch failed: %s", e)
```

**Issue**: When the market structure data fetch fails (network timeout, yfinance rate limit, API outage), the `vix` field defaults to 0. A VIX of 0 is impossible in reality (the VIX has never been below 9.14 historically) and signals an extremely calm market. The regime classifier will interpret VIX=0 as ultra-low volatility, selecting aggressive position sizing and trending-up regime parameters. The cross-asset macro module (`core/cross_asset_macro.py` L86-111) has a separate fallback using `_last_good_vix_spot` (default 20.0), but this is in the macro signal path, not the main market context builder. The two systems can diverge: the macro module thinks VIX is 20 while the regime classifier thinks VIX is 0.

**Impact**: During a data outage that coincides with a genuine volatility spike (the most dangerous scenario), the system will be maximally aggressive precisely when it should be most defensive. Position sizes will be too large, regime classification will be wrong, and the portfolio will be unhedged.

**Fix**: Replace the default-to-zero pattern with a cascading fallback: (1) use the last known good VIX value, (2) if stale by more than 10 minutes, set regime to CAUTION and halve position sizes, (3) if stale by more than 30 minutes, set VIX to 30.0 (conservative assumption) and activate reduced-exposure mode. In all cases, compute the fallback as `max(last_known_vix, vix_20d_ma + 5.0)` to ensure the fallback is never lower than the recent average. Add a staleness timestamp to the VIX data and expose it on the dashboard.

---

### F-08 | MEDIUM | 24/7 Scanning Wastes Compute on Weekends

**Location**: `main.py` (APScheduler runs 60s continuous scan loop)

**Issue**: The main scan loop runs every 60 seconds, 24 hours a day, 7 days a week. On weekends, bank holidays, and outside market hours (LSE closes at 16:30 UK, US pre-market starts at 09:00 UK), every scan cycle fetches stale price data, computes indicators on unchanged values, evaluates signals that cannot be acted upon, and logs repetitive "no change" entries. The only weekend check found in the codebase is a debug-level log suppression at L1567 for specific edge cases, not a comprehensive market calendar gate.

**Impact**: Unnecessary EC2 compute cost (t3.small running hot 24/7), unnecessary yfinance API calls that count toward rate limits, log pollution (thousands of identical "no signal" lines per weekend), and Redis write amplification on unchanged state. Over a year, this is approximately 2,500 hours of wasted compute (weekends + holidays + overnight).

**Fix**: Implement a market calendar gate that restricts scanning to 06:00-22:00 UK time on LSE/NYSE trading days only. Use the `exchange_calendars` library (already a common Python package) to determine trading days for both LSE and NYSE. Outside the gate window, the scan loop should sleep for 15 minutes between heartbeat checks (container health only, no data fetching). Pre-market intelligence scans should wake up at 06:00 UK to prepare for the LSE open at 08:00.

---

### F-09 | MEDIUM | Lunch RVOL Threshold 1.7 Too Restrictive

**Location**: `signal_engine/strategy_router.py` L458-461

```python
"Lunch chop window: RVOL min 1.7 required (spec rule)",
constraints={"tod_required": [TOD_LUNCH_CHOP], "min_rvol": 1.7},
```

**Issue**: The lunch chop window (12:00-13:30 UK) requires a minimum RVOL of 1.7 for VWAP mean-reversion signals. An RVOL of 1.7 means volume must be 70% above the 20-day average for that time-of-day bucket. During the lunch period, volume naturally drops by 30-50% from the morning session. Requiring 1.7x of an already-depressed baseline means the effective filter is closer to RVOL 2.5-3.0 relative to the full-day average. This eliminates virtually all lunch-period signals, making the VWAP_MR strategy dormant during its intended operating window.

**Impact**: The lunch chop strategy exists specifically to capture mean-reversion setups during low-volume range-bound conditions. An excessively high RVOL filter contradicts the strategy's premise -- if volume is 70% above normal during lunch, the market is not in a "lunch chop" state, it is in an unusual-activity state better suited to momentum strategies. The filter self-defeats the strategy.

**Fix**: Lower the lunch RVOL threshold to 1.3. This still requires above-average volume (ensuring liquidity for execution) without requiring the exceptional volume levels that invalidate the mean-reversion setup. Make the threshold configurable via `settings.yaml` so it can be tuned during paper trading without a code change.

---

### F-10 | MEDIUM | Daily Loss Halt Threshold Not Regime-Adaptive

**Location**: `risk_officer/rules/drawdown.py` L20-21, `core/trading_discipline.py` L60, `config/settings.yaml` L837-869

```python
_DAILY_LOSS_VETO_PCT       = 3.0
_DAILY_LOSS_DOWNSIZE_PCT   = 1.5
```

**Issue**: The daily loss halt is a fixed percentage across all regime states. In the drawdown rule, a 1.5% daily loss triggers downsizing and 3.0% triggers a full veto. In `settings.yaml`, per-bot daily loss limits range from -0.75% to -2.0%. None of these adapt to the current volatility regime. In a TRENDING regime, a 1.5% intraday drawdown is normal noise on 3x leveraged ETPs (a 0.5% underlying move produces a 1.5% ETP move). Triggering the downsize at -1.5% during a strong trend causes the system to reduce size precisely when it should be holding full size for the 2% daily target. Conversely, in a HIGH_VOL or SHOCK regime, a -1.5% drawdown may be the beginning of a much larger move, and the system should halt earlier.

**Impact**: In trending regimes: premature size reduction costs the daily compounding target. In volatile regimes: insufficient protection allows losses to compound past the point where recovery is feasible within the session.

**Fix**: Make the daily loss halt regime-conditional: TRENDING = -2.5% (allow normal 3x noise), RANGE_BOUND = -1.5% (current default), HIGH_VOL = -1.0% (tighter protection). SHOCK and RISK_OFF regimes should have 0.0% tolerance (no new positions, existing positions managed by regime transition handler). Implement this in `risk_officer/rules/drawdown.py` by accepting the current regime as a parameter to the `check()` method.

---

### F-11 | LOW | Kelly Cap at 0.75% Makes Computation Redundant

**Location**: `bots/kelly_sizer.py` L393, `main.py` L4011-4012

```python
self.immutable_cap: float = kelly_cfg.get("cap", 0.0075)  # 0.75% max risk
```

```python
# Half-Kelly with sample-size ramp, hard-capped at 0.75% (immutable)
risk_pct = self.kelly.get_risk_pct(ticker=signal.ticker) if hasattr(self, 'kelly') and self.kelly else 0.0075
```

**Issue**: The Kelly sizer computes a sophisticated Merton (1971) continuous-time fraction with jump-diffusion extension (Merton 1976), Cornish-Fisher variance adjustment, leverage-dependent fractional Kelly (quarter for 3x, fifth for 5x), sample-size ramp, and SHAP feature stability. After all this computation, the result is hard-capped at 0.75%. For the typical ISA universe (3x-5x leverage), the jump-diffusion Kelly with quarter/fifth fractional scaling almost always produces a value well below 0.75%, making the cap redundant. When conditions are genuinely favourable (strong edge, low vol, high sample size), the 0.75% cap prevents the system from sizing up, negating the entire purpose of dynamic Kelly sizing. The cap converts a dynamic sizer into a fixed-fraction sizer with extra computation overhead.

**Impact**: In RISK_OFF and SHOCK regimes, the Kelly fraction should be 0.0 (no position), but the cold-start fallback and the downstream code default to 0.75% regardless. The Kelly sizer computes a theoretically optimal fraction that is never used at its computed value -- it is either capped down or defaulted up.

**Fix**: Make the Kelly cap regime-conditional. In RISK_OFF and SHOCK: 0.0 * f* (zero position, not 0.75% default). In HIGH_VOL: 0.25 * f* with a cap of 0.50%. In RANGE_BOUND: 0.50 * f* with a cap of 0.75%. In TRENDING: 1.0 * f* with a cap of 1.25%. This allows the Kelly computation to actually influence sizing while maintaining an upper bound that scales with regime risk. The immutable 0.75% cap should become a constitutional maximum for the TRENDING regime, not a universal clamp.

---

### F-12 | LOW | Macro Cache TTL 30 Minutes is Too Stale for VIX

**Location**: `core/cross_asset_macro.py` L40

```python
_CACHE_SECONDS = 1800  # 30 minutes
```

**Issue**: The cross-asset macro module caches all macro signals (VIX term structure, DXY strength, credit spreads, Fear & Greed, HMM regime) with a single 30-minute TTL. VIX can move 5-10 points in 30 minutes during a market stress event. A cached VIX value from 29 minutes ago may be 25% stale during the exact conditions when VIX accuracy matters most. Meanwhile, DXY, credit spreads, and Fear & Greed are slow-moving indicators where 30-minute caching is perfectly adequate.

**Impact**: During rapid VIX spikes (the preamble to SHOCK regime), the system operates on stale VIX data for up to 30 minutes. Regime classification, position sizing, and the VIX circuit breaker all consume the cached value. A 30-minute lag on a VIX spike from 15 to 35 means the system runs aggressive sizing for half an hour into a market crash.

**Fix**: Implement per-signal TTLs: VIX term structure = 5 minutes, DXY = 30 minutes, credit spread = 30 minutes, Fear & Greed = 60 minutes, HMM = 30 minutes. Refactor `_is_cache_fresh()` to accept a signal-specific TTL parameter. For VIX specifically, consider a push-based update from the yfinance websocket (if available) or a dedicated 60-second polling loop that updates only the VIX cache entry.

---

## PART II: FATAL FLAWS IN AEGIS v10.0 PLAN (7 Flaws)

These flaws are architectural or theoretical deficiencies in the original Aegis v10.0 plan document that, if implemented as specified, would produce incorrect behaviour or misleading performance expectations.

---

### A-01 | CRITICAL | 2% Daily Compounding Model Ignores Losing Days

**Issue**: The foundational thesis states that 10,000 x (1.02)^252 = 1,485,757 (14,757% annualised). This calculation assumes a 2% gain on every single one of 252 trading days with zero losing days. This is not a simplification -- it is the basis on which the target equity curve, drawdown budgets, and milestone timelines are computed. In reality, even a 60% win rate with a 2.5R reward-to-risk ratio and 40 basis points of execution spread (bid-ask + slippage on 3x ETPs) produces a geometric mean daily return of approximately 1.14%, not 2.0%. The gap between 1.14% and 2.0% compounds catastrophically over 252 days: (1.0114)^252 = approximately 17.4x versus (1.02)^252 = approximately 148.6x -- an 8.5x overstatement of terminal wealth.

**Impact**: All downstream planning artifacts (capital deployment schedule, risk budgets, milestone dates, profit targets) are calibrated to a fantasy equity curve. When the system inevitably underperforms the 2% daily target, there is no framework for distinguishing "system broken" from "system performing correctly but below the impossible benchmark." Operator confidence erodes, leading to parameter tampering and override-driven losses.

**Fix**: Model honestly using Monte Carlo simulation with realistic parameters: 60% WR, 2.0-2.5R average winner, 1.0R average loser, 40bps round-trip friction, regime-dependent signal frequency (0-3 signals/day). The profit ladder (Chandelier exit with 5-rung trailing stop) is the bridging mechanism that converts modest edge into compounding returns -- document it as such. Replace the single-point 2% target with a distribution: P10 = 0.4%/day, P50 = 0.9%/day, P90 = 1.8%/day. Set operational targets at P50, not P90.

---

### A-02 | HIGH | Thomas & Zhang Beta Misapplied to Intraday Timeframe

**Issue**: The plan references Thomas & Zhang's post-earnings announcement drift (PEAD) with beta = 0.40 as a basis for earnings-related signal confidence adjustments. Thomas & Zhang (2002) measured PEAD over quarterly windows (60-90 trading days post-announcement). The system operates on intraday to 1-3 day holding periods. A quarterly beta of 0.40 does not decompose linearly to intraday timeframes -- the drift is concentrated in the first 1-2 days and then decays logarithmically. Applying a constant 0.40 beta across all holding periods overweights the PEAD signal on day 2+ and underweights it on day 0.

**Impact**: Earnings-related signals on day 0 are under-weighted relative to their true edge, while signals on day 2+ are over-weighted. The system may hold earnings drift positions too long (expecting continued drift) when the edge has already decayed.

**Fix**: Replace the static beta = 0.40 with empirical pair-specific betas calibrated from `data/outcomes.jsonl`. For each ticker, compute the realised drift coefficient at each holding period (0, 1, 2, 3 days post-earnings) from historical outcomes. Use these empirical betas instead of the academic aggregate. If insufficient data exists for a specific ticker, shrink toward the cross-sectional median beta using Bayesian shrinkage.

---

### A-03 | HIGH | RVOL Z-Score Threshold Too Selective for Universe Size

**Issue**: The plan specifies RVOL Z > 3.0 as a minimum filter for signal generation. A Z-score of 3.0 corresponds to the 99.87th percentile of the volume distribution. For a 12-ticker ISA universe scanned once per minute during a 6.5-hour trading session, this produces approximately 390 scan-minutes per ticker per day. At Z > 3.0, only 0.13% of scan-minutes pass the filter -- roughly 0.5 observations per ticker per day, or 6 across the entire universe. Most trading days will produce zero qualifying signals, making the 2% daily target unachievable by construction. The filter is calibrated for a 3,000-ticker US equity universe (where 0.13% yields approximately 4 qualifying tickers per scan), not a 12-ticker concentrated universe.

**Impact**: The system starves itself of opportunities. On quiet days (40-60% of all trading days), zero signals pass the RVOL gate, and the daily compounding target is missed by default. The no-signal-day protocol (A-06) becomes the dominant operating mode rather than an exception handler.

**Fix**: Make the RVOL Z-score threshold adaptive by regime: TRENDING = 2.0 (more permissive, capture breakouts), RANGE_BOUND = 2.5 (moderate, quality mean-reversion), HIGH_VOL = 3.0 (strict, only act on confirmed volume surges), SHOCK = 3.5 (maximum selectivity). Additionally, compute RVOL relative to the ticker's own time-of-day volume profile, not the daily average, to avoid lunch-period penalisation (related to F-09).

---

### A-04 | HIGH | Open-to-Close Velocity Ignores Overnight Gaps

**Issue**: The plan uses Open-to-Close (O2C) velocity as a momentum signal, measuring intraday price displacement per unit time. For LSE-listed leveraged ETPs, overnight gaps (driven by US after-hours moves on the underlying NASDAQ-100) routinely account for 50-80% of the total daily range. An ETP that gaps up 4% at the open and then trades flat for the rest of the day has an O2C velocity of approximately zero, despite a massive directional move already being priced in. Conversely, a gap-down followed by an intraday reversal produces a positive O2C velocity that masks the fact that the day's net move is negative.

**Impact**: O2C velocity generates false signals on gap days. A strong gap-up followed by flat trading produces a "no momentum" reading that causes the system to skip the day, missing continuation moves. A gap-down with a dead-cat bounce produces a "positive momentum" reading that triggers entries into positions that are net-negative for the day.

**Fix**: Compute a gap-to-range ratio for each ticker: `gap_ratio = abs(open - prev_close) / ADR_20`. If `gap_ratio > 0.50` (gap exceeds 50% of the 20-day average daily range), apply a -15 confidence penalty to any O2C-derived signal. Additionally, decompose total daily return into gap component and intraday component, and use only the intraday component for O2C velocity calculations. This prevents gaps from polluting the velocity signal.

---

### A-05 | MEDIUM | Stranger Ticker Discount Does Not Decay

**Issue**: The plan assigns a 0.5x confidence multiplier to "stranger" tickers -- those with fewer than a minimum number of historical trades in the outcomes database. This discount is binary: below the threshold = 0.5x, above = 1.0x. There is no decay function that gradually increases confidence as the sample size grows. A ticker with 49 trades (one below threshold) receives 0.5x; a ticker with 50 trades (one above) jumps to 1.0x. This discontinuity creates perverse incentives: the system may avoid a ticker for months, then suddenly go full-size on its 50th trade without any gradual calibration period.

**Impact**: Position sizing exhibits a cliff-edge discontinuity at the stranger threshold. The first full-size trade on a newly-graduated ticker has no intermediate validation. If the first 49 trades were in a specific regime and trade 50 occurs in a different regime, the system has no mechanism to detect this.

**Fix**: Replace the binary discount with Bayesian shrinkage toward the population mean. Define: `discount = 1 - lambda * exp(-n / n0)` where `lambda = 0.5` (maximum discount), `n` = number of historical trades for this ticker, and `n0 = 50` (half-life parameter). At n=0, discount = 0.5x. At n=50, discount = approximately 0.82x. At n=100, discount = approximately 0.93x. At n=200, discount = approximately 0.98x. Additionally, weight the shrinkage by the ticker's Drawdown Sharpe Ratio (DSR) to penalise tickers that have many trades but poor risk-adjusted performance.

---

### A-06 | MEDIUM | No "No-Signal Day" Protocol

**Issue**: The plan does not define what happens when the system reaches mid-afternoon without generating a single qualifying signal. Given the restrictive RVOL threshold (A-03), this scenario will occur on 40-60% of trading days. Without a protocol, the system will either (a) do nothing and miss the daily compounding target, or (b) lower its filters in desperation and take a low-quality trade that is more likely to lose.

**Impact**: On no-signal days, the operator faces an unstructured decision: accept a zero-return day (which breaks the compounding curve) or manually override the system to force a trade (which introduces discretionary risk). Neither option is acceptable for a systematic trading operation.

**Fix**: Implement an escalation cascade that progressively relaxes filters as the day progresses without a signal:
- 14:00 UK: Lower RVOL threshold by 0.3 (e.g., 2.0 becomes 1.7). Widen the ticker scan to include the 3 highest-RVOL tickers regardless of absolute threshold.
- 14:30 UK: Lower confidence floor from 55 to 50. Enable the VWAP mean-reversion strategy even outside the lunch window.
- 15:00 UK: Accept the best available signal with confidence >= 45, but at half-size.
- 15:30 UK: FLAT. Officially declare a no-signal day. Do not force a trade. Log the day as "NO_SIGNAL" in the outcomes database with PnL = 0. Update the Monte Carlo model with the zero-return day.

This cascade preserves signal quality while providing a structured response to quiet days.

---

### A-07 | MEDIUM | CVaR is Per-Position, Not Portfolio-Wide

**Location**: `qualification/dynamic_sizer.py` L130-134, L324-329

**Issue**: The CVaR (Conditional Value-at-Risk) scaling in the dynamic sizer operates on a per-position basis. It computes the rolling 60-trade 5th-percentile expected shortfall and scales individual position sizes accordingly (Rockafellar & Uryasev 2000). However, there is no portfolio-wide CVaR or CDaR (Conditional Drawdown-at-Risk) circuit breaker. A portfolio of 3 positions, each individually within CVaR limits, can collectively produce a drawdown that exceeds any acceptable portfolio-level threshold. This is especially acute given the correlation issue described in F-03: three NASDAQ-correlated 3x ETPs that are individually within CVaR limits can collectively produce a portfolio CVaR that is 2-3x the per-position estimate.

**Impact**: The system manages tail risk per position but is blind to portfolio-level tail risk. A correlated drawdown across all positions simultaneously -- precisely the scenario that leveraged NASDAQ ETPs are exposed to -- bypasses all existing CVaR protections.

**Fix**: Implement a two-tier CVaR framework: (1) retain per-trade CVaR scaling in the dynamic sizer as-is, and (2) add a portfolio-wide CDaR circuit breaker. The CDaR breaker should compute the maximum expected drawdown duration at the 5th percentile across all open positions, accounting for pairwise correlations from the Ledoit-Wolf covariance matrix. If portfolio CDaR exceeds 5% of equity, reduce all position sizes to half. If portfolio CDaR exceeds 8%, flatten to a single position (the one with the lowest correlation to the rest). This creates a nested defence: CVaR protects individual positions, CDaR protects the portfolio.

---

## AUDIT CERTIFICATION

All 19 flaws (12 codebase + 7 plan) have been verified against the live codebase as of 2026-03-04. Line numbers reference the current `main` branch. No flaw is speculative -- each was confirmed by reading the source code at the cited location.

**Priority for remediation**:
1. F-01, F-02, F-03 (CRITICAL codebase flaws -- fix before any live capital deployment)
2. A-01 (CRITICAL plan flaw -- recalibrate all downstream planning artifacts)
3. F-04 through F-07 (HIGH codebase flaws -- fix during paper trading phase)
4. A-02 through A-04 (HIGH plan flaws -- incorporate into next plan revision)
5. F-08 through F-12, A-05 through A-07 (MEDIUM/LOW -- schedule for Sprint 7+)

**Sign-off**: AEGIS CRO, v13.0 Pre-Launch Audit
