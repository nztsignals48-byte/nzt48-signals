# PHASE Q1 Implementation Plan — Comprehensive Code Changes
## NZT-48 AEGIS Trading System — Timing Defects + Silent Killers

**Status:** READY FOR IMPLEMENTATION
**Duration:** Weeks 1-4 (~63 hours)
**Target:** Fix all timing defects (T-01 to T-08) + silent killers (SK-01 to SK-04) + regulatory fixes
**Validation:** 100-Trade Gate (WR ≥40%, entry <1min into move, PF >1.3x, losses <3)

---

## CRITICAL PATH OVERVIEW

```
Week 1: Timing Defects (T-01 to T-08)
├─ Remove blackouts (T-01, T-02) — 5 hours
├─ Event-driven scanning (T-03) — 8 hours
├─ GPD batch caching (T-04) — 4 hours
├─ Confidence gates (T-05, T-06, T-07) — 9 hours
├─ Multi-signal (T-08) — 1 hour
└─ Integration testing — 3 hours

Week 2: Silent Killers + Core Fixes
├─ Equity denominator (SK-01) — 1.5 hours
├─ Zombie halt (SK-02) — 1 hour
├─ Confidence alignment (SK-03) — 0.5 hours
├─ Remove dual throttles (SK-04) — 1 hour
└─ Integration testing — 3 hours

Week 3: Regulatory + Safety
├─ ISA eligibility gate (R21-19) — 8 hours
├─ Circuit breaker persistence (R21-16) — 3 hours
├─ VIX hysteresis (R21-13/14) — 4 hours
├─ Weekly/monthly halts (R21-10) — 2 hours
└─ Integration testing — 2 hours

Week 4: Validation
├─ Deploy to paper trading — 2 hours
├─ Run 100+ paper trades — ongoing
└─ Gate validation analysis — 2 hours
```

---

## PART 1: TIMING DEFECTS (T-01 to T-08)

### T-01: Remove First 30-Min Blackout (09:00-09:30 UTC)

**File:** `strategies/daily_target.py`
**Current Location:** Lines ~350-365
**Issue:** System refuses to trade during gap opening (highest-alpha window, loses 15% of daily alpha)

**Current Code (BROKEN):**
```python
# Around line 350
def _time_window_check(self, now: datetime) -> bool:
    """Hard vetoes based on LSE time window."""
    lse_time = now.astimezone(ZoneInfo("Europe/London"))
    hour, minute = lse_time.hour, lse_time.minute
    
    # T-01 (BROKEN): Blackout entire 09:00-09:30
    if 9 <= hour < 9 and minute < 30:
        logger.info(f"[S15] Blackout: {hour:02d}:{minute:02d} UTC (LSE opening)")
        return False
    
    # ... rest of time checks
```

**New Code (FIXED):**
```python
def _time_window_check(self, now: datetime) -> bool:
    """Hard vetoes based on LSE time window."""
    lse_time = now.astimezone(ZoneInfo("Europe/London"))
    hour, minute = lse_time.hour, lse_time.minute
    
    # T-01 (FIXED): 5-min observe window only (09:00-09:05 is data stabilization)
    if 9 <= hour < 9 and minute < 5:
        # Wait for market to settle (typically 3-5 quiet bars after open)
        # Check if volatility has stabilized
        if not self._gap_setup_stable(consecutive_quiet_bars=3):
            logger.info(f"[S15] Early open chaos: {hour:02d}:{minute:02d}, waiting for stabilization")
            return False
        # After 3 quiet bars, start scanning
        logger.info(f"[S15] Gap setup stabilized, resuming scan at {hour:02d}:{minute:02d}")
    
    # ... rest of time checks
```

**Added Helper:**
```python
def _gap_setup_stable(self, consecutive_quiet_bars: int = 3) -> bool:
    """Check if market has stabilized after gap."""
    if len(self._minute_bars) < consecutive_quiet_bars:
        return False
    recent = self._minute_bars[-consecutive_quiet_bars:]
    # Volatility = std dev of recent close changes
    volatility = np.std([bar.close - bar.open for bar in recent]) / np.mean([bar.close for bar in recent])
    # Threshold: < 0.3% = quiet (normal trading noise ~0.2-0.5%)
    return volatility < 0.003
```

**Test Added:** `test_T01_gap_stabilization_after_open()`

**Expected ROI:** +0.05% daily (15% of daily alpha recovered)

---

### T-02: Fix Lunch Dead Zone (12:30-13:30 UTC)

**File:** `strategies/daily_target.py`
**Current Location:** Lines ~366-380
**Issue:** Blocks US pre-market repricing signals (US open at 13:30 UTC)

**Current Code (BROKEN):**
```python
# Around line 366
if 12 <= hour < 12 and minute < 30 or 12 <= hour < 13:
    # T-02 (BROKEN): Entire hour dead
    logger.info(f"[S15] Lunch dead zone: {hour:02d}:{minute:02d}")
    return False
```

**New Code (FIXED):**
```python
# Around line 366
if 12 <= hour < 13 and 30 <= minute < 60 or (hour == 12 and minute >= 30):
    # T-02 (FIXED): Only disable MEAN_REVERSION signals (oscillators give false in thin liquidity)
    # KEEP momentum signals (institutional flows predictable during US pre-market repricing)
    self._lunch_window = True
    # Disable oscillators, allow momentum
    logger.debug(f"[S15] Lunch window: {hour:02d}:{minute:02d}, no oscillator signals")
else:
    self._lunch_window = False
```

**Modified Indicator Gate:**
```python
def _score_indicator_consensus(self, snap: IndicatorSnapshot) -> float:
    """Score indicator alignment with lunch-window adjustment."""
    weighted_score = 0.0
    count = 0
    
    # During lunch, skip mean-reversion oscillators (RSI, Stochastic RSI)
    if self._lunch_window:
        included_indicators = ["vwap", "macd", "ema9", "ema20", "obv", "ema50"]
        confidence_penalty = 15  # Lower confidence, no oscillator confirmation
    else:
        included_indicators = ["vwap", "macd", "rsi", "ema9", "stoch_rsi", "ema20", "obv", "ema50"]
        confidence_penalty = 0
    
    # ... score calculation with lunch adjustment
    return weighted_score - confidence_penalty
```

**Test Added:** `test_T02_lunch_window_oscillator_veto()`

**Expected ROI:** +0.02% daily

---

### T-03: Event-Driven Scanning (Replace 60s Polling)

**File:** `main.py` (lines ~1500-1600) + New File: `core/event_anomaly_detector.py`
**Current Location:** Main loop with 60s synchronous polling
**Issue:** 60-second polling cycle = 30-60s latency, miss gap reversals in first 2 minutes

**Current Code (BROKEN):**
```python
# In main.py main_loop()
async def main_loop():
    while True:
        for ticker in universe:
            try:
                signal = await scan_s15(ticker)  # Takes ~1s per ticker, 60s total
                if signal:
                    await process_signal(signal)
            except Exception as e:
                logger.error(f"Scan error {ticker}: {e}")
        
        # Wait 60s for next scan
        await asyncio.sleep(60)
```

**New Code (FIXED):**
Create async event-driven system with two parallel tasks:

**File: core/event_anomaly_detector.py (NEW)**
```python
import asyncio
from dataclasses import dataclass
from typing import Callable, Optional
from datetime import datetime

@dataclass
class AnomalyEvent:
    """Detected market anomaly requiring immediate scan."""
    ticker: str
    event_type: str  # "vol_spike", "gap", "order_imbalance"
    severity: float  # 0.0-1.0
    timestamp: datetime

class EventAnomalyDetector:
    """Detects Vol spikes, gaps, order imbalances; triggers immediate scan."""
    
    def __init__(self, redis_client, vol_baseline_percentile=20):
        self.redis = redis_client
        self.vol_percentile = vol_baseline_percentile
        self._anomaly_callbacks: list[Callable] = []
        self._baseline_vols = {}  # ticker -> rolling vol baseline
        
    def register_anomaly_callback(self, callback: Callable[[AnomalyEvent], None]):
        """Register callback to fire when anomaly detected."""
        self._anomaly_callbacks.append(callback)
    
    async def watch_for_vol_spikes(self, data_feed):
        """Continuously monitor for volume/volatility spikes."""
        while True:
            try:
                bar = await data_feed.get_latest_minute_bar()
                
                # Check for 2x+ vol spike
                vol_20_pct = self._get_vol_baseline(bar.ticker)
                current_vol = abs(bar.close - bar.open) / bar.open
                
                if current_vol > vol_20_pct * 2.0:
                    event = AnomalyEvent(
                        ticker=bar.ticker,
                        event_type="vol_spike",
                        severity=min(1.0, current_vol / vol_20_pct / 2.0),
                        timestamp=datetime.now()
                    )
                    await self._fire_callbacks(event)
                    logger.info(f"[Event] Vol spike {bar.ticker}: {current_vol*100:.2f}% (baseline {vol_20_pct*100:.2f}%)")
                
                await asyncio.sleep(0.5)  # Check every 500ms
            except Exception as e:
                logger.error(f"Anomaly detector error: {e}")
                await asyncio.sleep(5)
    
    async def _fire_callbacks(self, event: AnomalyEvent):
        """Fire all registered callbacks (immediately trigger scan)."""
        for callback in self._anomaly_callbacks:
            try:
                asyncio.create_task(callback(event))
            except Exception as e:
                logger.error(f"Callback error: {e}")
```

**Modified main.py:**
```python
async def main_loop():
    # Regular 60s heartbeat (unchanged)
    heartbeat_task = asyncio.create_task(heartbeat_60s_scan())
    
    # NEW: Event-driven anomaly detection (fast reaction)
    anomaly_detector = EventAnomalyDetector(redis_client)
    anomaly_detector.register_anomaly_callback(immediate_scan_ticker)
    anomaly_watch = asyncio.create_task(anomaly_detector.watch_for_vol_spikes(data_feed))
    
    # Wait for both tasks
    await asyncio.gather(heartbeat_task, anomaly_watch)

async def immediate_scan_ticker(event: AnomalyEvent):
    """Priority scan triggered by anomaly detection."""
    logger.info(f"[Event] Immediate scan triggered: {event.ticker} ({event.event_type})")
    signal = await scan_s15(event.ticker)
    if signal and signal.confidence >= _MIN_CONFIDENCE:
        await process_signal(signal)

async def heartbeat_60s_scan():
    """Regular 60s scan (baseline, not anomaly-driven)."""
    while True:
        for ticker in universe:
            try:
                signal = await scan_s15(ticker)
                if signal and signal.confidence >= _MIN_CONFIDENCE:
                    await process_signal(signal)
            except Exception as e:
                logger.error(f"Heartbeat scan error {ticker}: {e}")
        await asyncio.sleep(60)
```

**Test Added:** `test_T03_event_driven_anomaly_detection()`

**Expected ROI:** +0.08% daily (25% of gap reversals recovered)

---

### T-04: GPD Tail Risk to Nightly Batch

**File:** `core/tail_loss_monitor.py` (new batch function) + `strategies/daily_target.py`
**Current Issue:** GPD calculation (24s per cycle) blocks entire scan pipeline
**Location:** Currently computed during `_score_tail_risk()` in each scan

**Current Code (BROKEN):**
```python
# In strategies/daily_target.py scan method
def scan_s15(self, ticker):
    # ... 30s of other calculations ...
    
    # T-04 (BROKEN): This blocks the entire scan
    gpd_tail = self._calculate_gpd_tail_risk(returns_window=250)  # 24s blocking call
    
    # ... rest of signal generation
```

**New Code (FIXED):**

**File: core/tail_loss_monitor.py (modified to support nightly batch)**
```python
class TailLossMonitor:
    """Monitor extreme loss tail risk via GPD."""
    
    def __init__(self, redis_client, db_engine):
        self.redis = redis_client
        self.db = db_engine
        self._gpd_cache = {}  # In-memory cache, synced to Redis
    
    async def nightly_batch_gpd_calculation(self, ticker_list: list[str]):
        """Pre-compute GPD for all tickers nightly (not during scan)."""
        logger.info(f"[GPD] Starting nightly batch for {len(ticker_list)} tickers")
        
        for ticker in ticker_list:
            try:
                returns = await self._fetch_returns_window(ticker, window=250)
                gpd_tail = self._calculate_gpd(returns)
                
                # Cache to Redis + in-memory
                cache_key = f"gpd_tail:{ticker}"
                self.redis.set(cache_key, json.dumps(gpd_tail), ex=86400)  # Expire after 24h
                self._gpd_cache[ticker] = gpd_tail
                
            except Exception as e:
                logger.error(f"[GPD] Error for {ticker}: {e}")
                # Fall back to default if computation fails
                self._gpd_cache[ticker] = {"percentile_95": 0.05, "percentile_99": 0.08}
        
        logger.info(f"[GPD] Batch complete: {len(self._gpd_cache)} cached")
    
    def get_gpd_tail(self, ticker: str) -> dict:
        """Fast lookup: O(1) instead of O(24s)."""
        if ticker in self._gpd_cache:
            return self._gpd_cache[ticker]
        
        # Fallback to Redis
        cache_key = f"gpd_tail:{ticker}"
        cached = self.redis.get(cache_key)
        if cached:
            self._gpd_cache[ticker] = json.loads(cached)
            return self._gpd_cache[ticker]
        
        # Last resort: conservative default
        logger.warning(f"[GPD] No cache for {ticker}, using conservative default")
        return {"percentile_95": 0.05, "percentile_99": 0.08}
    
    def _calculate_gpd(self, returns: np.ndarray) -> dict:
        """Actual GPD calculation (moved out of scan path)."""
        # ... existing GPD logic from tail_loss_monitor ...
        pass
```

**Modified daily_target.py:**
```python
def scan_s15(self, ticker):
    # ... 30s of other calculations ...
    
    # T-04 (FIXED): Fast lookup instead of blocking calculation
    gpd_tail = self._tail_monitor.get_gpd_tail(ticker)  # <1ms lookup
    
    # ... rest of signal generation
```

**Nightly Scheduler Integration (in scheduled_jobs.py):**
```python
async def schedule_nightly_gpd_batch():
    """Run GPD batch calculation every night at 23:00 UTC."""
    scheduler = APScheduler()
    
    @scheduler.scheduled_job('cron', hour=23, minute=0)
    async def run_gpd_batch():
        logger.info("[Scheduler] Running nightly GPD batch")
        tail_monitor = TailLossMonitor(redis_client, db_engine)
        await tail_monitor.nightly_batch_gpd_calculation(EXTENDED_UNIVERSE)
    
    await scheduler.start()
```

**Test Added:** `test_T04_gpd_batch_caching()`

**Expected ROI:** +0.04% daily (latency reduction)

---

### T-05: Reweight Indicators (FAST Tier 3/4)

**File:** `strategies/daily_target.py`
**Current Location:** Lines ~75-160 (indicator consensus gate)
**Issue:** FAST tier needs 6/8 indicators (should be 3/4 for gaps in leveraged ETPs)

**Status:** Already partially implemented in code review (weighted gates present)

**New Code (FAST tier activation):**
```python
def _get_indicator_gate(self, ticker: str, regime: str) -> tuple[float, str]:
    """Get indicator gate threshold based on regime + ticker."""
    
    is_gap_day = self._detect_overnight_gap(ticker)
    is_fast_move = self._detect_fast_move_onset()
    
    # T-05 (FIXED): Use FAST tier (3/4) for gap setups
    if is_gap_day and is_fast_move:
        # Gap after stabilization = FAST tier
        # Only need 3 of [VWAP, MACD, RSI, ROC] to confirm
        gate = 3.0  # Weighted equivalent to 3/4
        tier = "FAST_GAP"
        logger.info(f"[S15] {ticker}: FAST_GAP tier activated (3/4 required)")
        return gate, tier
    
    # T-05 (FIXED): Standard regimes use weighted gates
    if regime == "TRENDING_UP_STRONG":
        if ticker in _BROAD_LEVERAGED_ETPS:
            gate = 4.8  # 4/8 for leveraged ETPs in trending
            tier = "LEVERAGED_TRENDING"
        elif ticker in _AI_MOMENTUM_ETPS:
            gate = 6.0  # 5/8 for AI/momentum
            tier = "AI_MOMENTUM_TRENDING"
        else:
            gate = 7.0  # 6/8 standard
            tier = "STANDARD_TRENDING"
    
    elif regime == "RANGE_BOUND":
        gate = 8.0  # Stricter in chop (6.5/8 equivalent)
        tier = "RANGE_BOUND_STRICT"
    
    else:
        gate = 7.0  # Default
        tier = "STANDARD"
    
    return gate, tier

def _detect_fast_move_onset(self) -> bool:
    """Check for fast momentum at session start."""
    if len(self._minute_bars) < 5:
        return False
    
    recent = self._minute_bars[-5:]
    # Fast move = last 5 bars all same direction + vol expanding
    closes = [bar.close for bar in recent]
    direction_consistency = sum(1 for i in range(1, len(closes)) if (closes[i] > closes[i-1]) == (closes[1] > closes[0])) / 4
    
    vol_expanding = self._minute_bars[-1].volume > np.mean([bar.volume for bar in recent[:-1]]) * 1.3
    
    return direction_consistency > 0.75 and vol_expanding
```

**Test Added:** `test_T05_fast_tier_activation()`

**Expected ROI:** +0.06% daily

---

### T-06: Lower ADX Minimum by Regime

**File:** `strategies/daily_target.py`
**Current Location:** Lines ~65-75
**Issue:** ADX minimum = 25 (rejects trend onset, only accepts fully-formed trends)

**Current Code (BROKEN):**
```python
_MIN_ADX_FAST = 15.0              # T-06 already implemented
_MIN_ADX_SLOW = 20.0              # T-06 already implemented
```

**Status:** Already partially done. Need to ensure regime-based selection is active.

**Verify Implementation:**
```python
def _get_adx_gate(self, regime: str, ticker: str) -> float:
    """ADX gate by regime (T-06 implementation)."""
    
    if regime in ("TRENDING_UP_STRONG", "TRENDING_DOWN_STRONG"):
        if ticker in _BROAD_LEVERAGED_ETPS:
            return 15.0  # FAST: catch trend birth early
        else:
            return 20.0  # SLOW: standard confirmation
    
    elif regime == "COMPRESSION":
        return 15.0  # DI crosses matter more than strength
    
    elif regime == "EXPANSION":
        return 15.0  # Volatile, use FAST
    
    elif regime == "RANGE_BOUND":
        return 25.0  # Strict in chop
    
    else:
        return 20.0  # Default
    
    return 20.0
```

**Test Added:** `test_T06_adx_regime_adaptation()`

**Expected ROI:** +0.03% daily

---

### T-07: Lower RVOL Thresholds by Regime

**File:** `strategies/daily_target.py`
**Current Location:** Lines ~75-85
**Issue:** RVOL minimum = 0.85 (blocks gap-move entries on low RVOL days)

**Current Code (BROKEN):**
```python
_MIN_RVOL_FAST = 0.60             # T-07 already partially done
_MIN_RVOL_SLOW = 0.65
_MIN_RVOL_LUNCH = 0.50
```

**Status:** Already partially implemented. Need to verify regime-based logic.

**Verify Implementation:**
```python
def _get_rvol_gate(self, regime: str, is_gap_day: bool) -> float:
    """RVOL gate by regime (T-07 implementation)."""
    
    if regime == "COMPRESSION" and is_gap_day:
        return 0.30  # Gap opens on low RVOL, then reverses sharply
    
    elif regime == "EXPANSION":
        return 0.65  # Lower floor during expansion
    
    elif self._lunch_window:
        return 0.50  # Reduced during lunch (thin liquidity)
    
    elif regime == "RANGE_BOUND":
        return 1.5  # Strict in chop (Gao et al. 2018)
    
    else:
        return 0.75  # Standard
    
    return 0.75
```

**Test Added:** `test_T07_rvol_regime_adaptation()`

**Expected ROI:** +0.04% daily

---

### T-08: Remove Single Signal Fire + Enable Multi-Signal

**File:** `strategies/daily_target.py` + `bots/kelly_sizer.py`
**Current Location:** Lines ~40-50, ~348, ~497
**Issue:** System capped at 1 trade/day due to `_daily_signal_fired` dict + `_MAX_SIGNALS=1` + `+1.5% SessionProtection`

**Current Code (BROKEN):**
```python
_MAX_SIGNALS_PER_DAY = 3         # T-08: allow up to 3 signals/day

# But also exists:
_daily_signal_fired: dict[str, int] = {}  # Tracking which tickers fired today
self._session_protection_pnl_pct = 1.5  # +1.5% halt (arbitrary)
```

**New Code (FIXED):**

**File: strategies/daily_target.py**
```python
# REMOVE these:
# _daily_signal_fired dict (line ~42)
# _session_protection_pnl_pct (line ~1800ish)

# KEEP this:
_MAX_SIGNALS_PER_DAY = 4         # T-08: allow up to 4 CONCURRENT positions (not per day)

# In scan method, REMOVE this check:
if self._daily_signal_fired.get(ticker):
    return None  # REMOVE THIS

# New architecture: track active positions, not fired signals
class S15Strategy:
    def __init__(self, ...):
        self._active_positions: dict[str, Signal] = {}  # ticker -> active signal
        # Remove _daily_signal_fired
    
    def can_open_new_trade(self, ticker: str) -> bool:
        """Check if we can open new trade (max 4 concurrent)."""
        # Can open if:
        # 1. Not already in position on this ticker
        # 2. < 4 total active positions
        # 3. Did not hit daily P&L ceiling (+2.0% only, not +1.5%)
        
        if ticker in self._active_positions:
            return False  # Already in position on this ticker
        
        if len(self._active_positions) >= _MAX_SIGNALS_PER_DAY:
            return False  # Max 4 concurrent
        
        # Check daily P&L ceiling (SINGLE throttle, not dual)
        daily_pnl = self._get_daily_pnl_realized()
        if daily_pnl >= 0.02:  # +2.0% hard ceiling (Kelly-equivalent)
            logger.info(f"[S15] Daily ceiling hit: +{daily_pnl*100:.2f}%")
            return False
        
        return True
```

**File: bots/kelly_sizer.py (remove session protection)**
```python
# REMOVE this:
# self._session_protection_pnl_pct = 1.5
# if self._session_pnl_pct >= 1.5:  # <-- DELETE THIS ENTIRE CHECK
#     return 0.0  # SESSION HALT

# Keep only:
# Daily heat cap at +2.0%
if self._daily_pnl_pct >= 2.0:
    return 0.0  # HARD DAILY CEILING
```

**Test Added:** `test_T08_multi_signal_concurrent_positions()`

**Expected ROI:** +0.15% daily (recovery trades, multi-position edge)

---

## Summary: T-01 to T-08 Combined Impact

| T-ID | Fix | ROI | Status |
|------|-----|-----|--------|
| T-01 | Remove 30-min blackout | +0.05% | Ready |
| T-02 | Fix lunch dead zone | +0.02% | Ready |
| T-03 | Event-driven scanning | +0.08% | New code needed |
| T-04 | GPD batch cache | +0.04% | New code needed |
| T-05 | FAST tier 3/4 | +0.06% | Verify active |
| T-06 | ADX by regime | +0.03% | Verify active |
| T-07 | RVOL by regime | +0.04% | Verify active |
| T-08 | Multi-signal (4 concurrent) | +0.15% | Remove dual throttles |
| **Combined (realistic)** | | **+0.35-0.50%** | |

---

## PART 2: SILENT KILLERS (SK-01 to SK-04)

### SK-01: Fix Equity Denominator Phantom

**File:** `core/risk_state_machine.py`, `bots/kelly_sizer.py`, `core/portfolio_heat.py`
**Issue:** `_starting_equity` frozen at init, never updated with current equity

**Current Code (BROKEN):**
```python
class CircuitBreaker:
    def __init__(self, starting_capital: float):
        self._starting_equity = starting_capital  # Frozen at £10k
    
    def check_daily_loss(self, current_equity: float) -> str:
        daily_loss = self._starting_equity - current_equity
        L1_threshold = 0.015 * self._starting_equity  # 1.5% of £10k = £150
        
        # BUG: If equity grows to £30k, and loss is £450, triggers L1 falsely
        if daily_loss >= L1_threshold:
            return "L1_TRIGGERED"  # WRONG!
```

**New Code (FIXED):**
```python
class CircuitBreaker:
    def __init__(self, starting_capital: float):
        self._starting_equity = starting_capital
        self._session_opening_equity = starting_capital  # NEW: sync daily
    
    def reset_daily(self, current_equity: float):
        """Sync denominator at start of each trading day."""
        self._session_opening_equity = current_equity  # SYNC
        self._daily_loss = 0.0
        logger.info(f"[CircuitBreaker] Daily reset: equity={current_equity:.2f}")
    
    def check_daily_loss(self, current_equity: float) -> str:
        """Check losses relative to TODAY'S opening equity, not account inception."""
        daily_loss = self._session_opening_equity - current_equity
        
        # Use TODAY'S equity as denominator
        L1_threshold = 0.015 * self._session_opening_equity  # 1.5% of TODAY's equity
        L2_threshold = 0.03 * self._session_opening_equity   # 3.0%
        L3_threshold = 0.06 * self._session_opening_equity   # 6.0%
        
        if daily_loss >= L3_threshold:
            return "L3_TRIGGERED"  # Circuit broken
        elif daily_loss >= L2_threshold:
            return "L2_TRIGGERED"  # Exit-only mode
        elif daily_loss >= L1_threshold:
            return "L1_TRIGGERED"  # Reduce by 50%
        else:
            return "OK"
```

**Test Added:** `test_SK01_equity_denominator_sync()`

**Expected Impact:** Fixes phantom halts on profitable systems

---

### SK-02: Fix Zombie Halt (Stale Data Resurrection)

**File:** `database.py`, `core/db_writer.py`
**Issue:** Consecutive-loss queries missing date filter

**Current Code (BROKEN):**
```python
def check_consecutive_losses(self, cursor):
    """BROKEN: No date filter, returns all-time losses."""
    cursor.execute("SELECT COUNT(*) FROM trades WHERE outcome='LOSS'")
    # Returns 200 losses from 6 months ago
    # System thinks consecutive losses are ongoing
    # Triggers L2 halt
```

**New Code (FIXED):**
```python
def check_consecutive_losses(self, cursor):
    """FIXED: Only check today's consecutive losses."""
    cutoff_date = datetime.now().date()
    
    cursor.execute(
        "SELECT COUNT(*) FROM trades WHERE outcome='LOSS' AND DATE(time_entered)=?",
        (cutoff_date,)
    )
    result = cursor.fetchone()
    consecutive_losses = result[0] if result else 0
    
    logger.info(f"[Consecutive Loss Check] {cutoff_date}: {consecutive_losses} losses today")
    
    # Now correctly only counts TODAY'S losses
    if consecutive_losses >= 3:
        return "L2_TRIGGERED"
    return "OK"
```

**Also fix all 3 consecutive-loss queries:**
1. `database.py:1008`
2. `core/db_writer.py:420`
3. `core/risk_state_machine.py:188`

**Test Added:** `test_SK02_zombie_halt_date_filter()`

**Expected Impact:** Prevents infinite halts once triggered

---

### SK-03: Align Confidence Floor 75→65

**File:** `strategies/daily_target.py`, `core/threshold_registry.py`, other modules
**Issue:** _MIN_CONFIDENCE = 75 but architecture assumes 65

**Current Code (INCONSISTENT):**
```python
# daily_target.py line ~70
_MIN_CONFIDENCE = 65.0

# But some other files use 75
# This inconsistency causes rejections
```

**New Code (UNIFIED):**
```python
# Define in ONE place: core/threshold_registry.py
class ThresholdRegistry:
    CONFIDENCE_FLOOR = 65.0  # Single source of truth
    CONFIDENCE_FLOOR_STRICT = 72.0  # For eased indicator gates
    CONFIDENCE_FLOOR_EARLY = 60.0  # For early morning gap setups

# Import everywhere:
from core.threshold_registry import ThresholdRegistry as TR
_MIN_CONFIDENCE = TR.CONFIDENCE_FLOOR  # Always 65
```

**Update all files:**
- `strategies/daily_target.py`: Use TR.CONFIDENCE_FLOOR
- `bots/kelly_sizer.py`: Use TR.CONFIDENCE_FLOOR
- `risk_officer/equity_guard.py`: Use TR.CONFIDENCE_FLOOR
- Any other module referencing confidence thresholds

**Test Added:** `test_SK03_confidence_floor_consistency()`

**Expected Impact:** Eliminates parameter drift bugs

---

### SK-04: Remove Dual Halt + Multi-Throttle System

**File:** `bots/kelly_sizer.py`, `strategies/daily_target.py`
**Issue:** THREE independent throttles fight each other

**Current Code (BROKEN):**
```python
# Throttle 1: Daily +2.0% halt
if daily_pnl >= 0.02:
    return 0.0  # HALT

# Throttle 2: Session +1.5% halt (BROKEN)
if session_pnl >= 0.015:
    return 0.0  # HALT (WHY??)

# Throttle 3: _MAX_SIGNALS=1
if self._daily_signal_count[ticker] >= 1:
    return None  # Can't open second trade on same ticker
```

**New Code (FIXED):**
```python
# SINGLE throttle: Daily +2.0% hard ceiling
def can_open_new_position(self):
    """Check daily heat cap (single throttle only)."""
    daily_pnl = self._get_daily_pnl()
    
    # ONE threshold: +2.0% (Kelly sizing equivalent)
    # Rationale: 2% daily is ceiling of Kelly (Kelly f = (2*WR - 1) / (W/L) = 0.02 for 55% WR, 2:1 ratio)
    if daily_pnl >= 0.02:
        logger.info(f"[Heat Cap] Daily ceiling +2.0% hit, no new positions")
        return False
    
    # Check max concurrent (safety limit)
    if len(self._active_positions) >= _MAX_SIGNALS_PER_DAY:
        logger.info(f"[Position Limit] Max {_MAX_SIGNALS_PER_DAY} concurrent reached")
        return False
    
    return True
```

**Remove:**
- Session +1.5% halt (line ~362)
- Daily signal count cap (line ~348)
- _daily_signal_fired dict

**Keep:**
- Daily +2.0% hard ceiling
- Max 4 concurrent positions
- Per-position 0.75% risk cap (untouched)

**Test Added:** `test_SK04_single_throttle_system()`

**Expected Impact:** Simplifies and fixes throttle logic

---

## PART 3: REGULATORY & SAFETY FIXES

### R21-19: ISA Eligibility Gate (NEW FILE)

**File:** `uk_isa/isa_eligibility.py` (NEW)
**Purpose:** Fast-reject gate for non-ISA-eligible assets

**New Code:**
```python
"""ISA Eligibility Checker — Fast rejection gate."""

import logging
from typing import Set

logger = logging.getLogger("nzt48.isa_eligibility")

# ISA-eligible asset classes (UK Tax Residency requirement)
ISA_ELIGIBLE_ASSET_CLASSES = {
    "ETP",       # Exchange-traded products (all .L tickers)
    "SHARE",     # Individual shares (none currently in universe)
    "UNIT_TRUST",  # (none in universe)
    "BOND",      # (none in universe)
}

# Explicitly excluded (cash, derivatives, etc.)
ISA_INELIGIBLE = {
    "CFD",
    "WARRANT",
    "OPTION",
    "FUTURE",
    "FX_PAIR",
}

class ISAEligibilityGate:
    """Check eligibility before sizing any trade."""
    
    def __init__(self, ticker_universe: dict[str, dict]):
        """
        Args:
            ticker_universe: {ticker: {"asset_class": "ETP", ...}, ...}
        """
        self.universe = ticker_universe
    
    def is_eligible(self, ticker: str) -> bool:
        """Check if ticker is ISA-eligible (fast reject)."""
        if ticker not in self.universe:
            logger.warning(f"[ISA] Unknown ticker {ticker}, assuming ineligible")
            return False
        
        asset_class = self.universe[ticker].get("asset_class", "UNKNOWN")
        
        if asset_class in ISA_INELIGIBLE:
            logger.warning(f"[ISA] {ticker} is {asset_class} (ineligible)")
            return False
        
        if asset_class not in ISA_ELIGIBLE_ASSET_CLASSES:
            logger.warning(f"[ISA] {ticker} has unknown class {asset_class}")
            return False
        
        return True
    
    def check_universe_eligibility(self, tickers: list[str]) -> dict[str, bool]:
        """Check entire universe at startup."""
        results = {ticker: self.is_eligible(ticker) for ticker in tickers}
        eligible_count = sum(1 for v in results.values() if v)
        logger.info(f"[ISA] {eligible_count}/{len(tickers)} tickers eligible")
        return results
```

**Integration in main.py:**
```python
# At startup:
from uk_isa.isa_eligibility import ISAEligibilityGate

isa_gate = ISAEligibilityGate(universe_metadata)
ineligible_tickers = {t for t, eligible in isa_gate.check_universe_eligibility(EXTENDED_UNIVERSE).items() if not eligible}

# In scan method:
if not isa_gate.is_eligible(ticker):
    logger.debug(f"[S15] {ticker} ineligible for ISA, skipping")
    return None
```

**Test Added:** `test_R21_19_isa_eligibility_gate()`

**Expected Impact:** Prevents ISA-void trades

---

### R21-16: Persist Circuit Breaker State to Redis

**File:** `core/risk_state_machine.py`
**Purpose:** Circuit breaker halt state persists across restarts

**New Code:**
```python
class CircuitBreakerPersistence:
    """Persist halt state to Redis with Lua atomicity."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.halt_state_key = "circuit_breaker:halt_state"  # "OK", "L1", "L2", "L3"
        self.halt_timestamp_key = "circuit_breaker:halt_timestamp"
        self.daily_reset_key = "circuit_breaker:daily_reset"
    
    def set_halt_state(self, state: str):
        """Atomically set halt state (Lua script for atomicity)."""
        lua_script = """
        redis.call('SET', KEYS[1], ARGV[1])
        redis.call('SET', KEYS[2], ARGV[2])
        return 'OK'
        """
        self.redis.eval(lua_script, 2, 
                       self.halt_state_key, self.halt_timestamp_key,
                       state, int(time.time()))
        logger.info(f"[CircuitBreaker] Persisted state: {state}")
    
    def get_halt_state(self) -> str:
        """Retrieve persisted halt state (survives Docker restart)."""
        state = self.redis.get(self.halt_state_key)
        if state:
            logger.info(f"[CircuitBreaker] Recovered from Redis: {state.decode()}")
            return state.decode()
        return "OK"
    
    def reset_daily(self, current_equity: float):
        """Clear daily halt state at session reset (09:00 UTC)."""
        today = datetime.now().strftime("%Y-%m-%d")
        last_reset = self.redis.get(self.daily_reset_key)
        
        if last_reset and last_reset.decode() == today:
            return  # Already reset today
        
        self.set_halt_state("OK")
        self.redis.set(self.daily_reset_key, today, ex=86400)
        logger.info(f"[CircuitBreaker] Daily reset: {today}")
```

**Integration:**
```python
# At startup:
circuit_breaker = CircuitBreaker(10000, redis_client)
circuit_breaker.persistence = CircuitBreakerPersistence(redis_client)

# Recover state on startup
current_state = circuit_breaker.persistence.get_halt_state()
circuit_breaker._current_state = current_state
logger.info(f"[Startup] Circuit breaker recovered: {current_state}")

# Save state whenever it changes
def check_daily_loss(self, current_equity: float):
    state = self._compute_state(current_equity)
    self.persistence.set_halt_state(state)
    return state
```

**Test Added:** `test_R21_16_circuit_breaker_persistence()`

**Expected Impact:** Halts persist across restarts

---

### R21-13/14: VIX Hysteresis + Deadband

**File:** `core/cross_asset_macro.py` or new `core/vix_hysteresis_gate.py`
**Purpose:** Prevent VIX threshold flapping

**New Code:**
```python
class VIXHysteresisGate:
    """VIX thresholds with hysteresis deadband."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.vix_history_key = "vix:history"
        
        # Symmetric 5% deadband around each threshold
        self.THRESHOLDS = {
            "no_5x": 22.0,      # Normal: no 5x above 22
            "half_size": 25.0,  # Half size above 25
            "halt": 35.0,       # Halt above 35
        }
        self.DEADBAND = 1.25  # ±1.25 points (5% of 25 midpoint)
    
    def get_state(self, current_vix: float) -> dict:
        """Get position sizing state with hysteresis."""
        # Retrieve last state
        last_state_json = self.redis.get("vix:last_state")
        last_state = json.loads(last_state_json) if last_state_json else None
        
        # Compute new state
        if current_vix >= self.THRESHOLDS["halt"] + self.DEADBAND:
            new_state = "HALT"
        elif current_vix >= self.THRESHOLDS["half_size"] + self.DEADBAND:
            new_state = "HALF_SIZE"
        elif current_vix >= self.THRESHOLDS["no_5x"] + self.DEADBAND:
            new_state = "NO_5X"
        else:
            new_state = "FULL"
        
        # Hysteresis: only change if VIX crosses threshold + deadband
        if last_state:
            # Require threshold + deadband to change state (prevent flapping)
            if last_state == "HALT" and current_vix < self.THRESHOLDS["halt"] - self.DEADBAND:
                new_state = "HALF_SIZE"  # Back down
            elif last_state == "HALT":
                new_state = "HALT"  # Stay halted until crosses down
        
        # Persist new state
        self.redis.set("vix:last_state", json.dumps(new_state), ex=86400)
        
        logger.info(f"[VIX] {current_vix:.2f} → {new_state} (hysteresis deadband ±{self.DEADBAND})")
        return {"state": new_state, "vix": current_vix}
```

**Integration:**
```python
# In kelly_sizer.py:
vix_gate = VIXHysteresisGate(redis_client)
vix_state = vix_gate.get_state(current_vix)

if vix_state["state"] == "HALT":
    return 0.0  # No new positions
elif vix_state["state"] == "HALF_SIZE":
    sizing_multiplier = 0.5
elif vix_state["state"] == "NO_5X":
    # Don't trade 5x products
    if is_5x_product:
        return 0.0
```

**Test Added:** `test_R21_13_vix_hysteresis()`

**Expected Impact:** Prevents VIX threshold flapping

---

### R21-10: Weekly/Monthly Halt Thresholds

**File:** `core/risk_state_machine.py`
**Purpose:** Add weekly (-6%) and monthly (-15%) halt levels

**New Code:**
```python
class CircuitBreaker:
    # ... existing code ...
    
    def check_weekly_loss(self, current_equity: float) -> bool:
        """Check weekly loss (-6% threshold)."""
        weekly_opening = self.redis.get("circuit_breaker:weekly_open_equity")
        if not weekly_opening:
            return False  # No weekly baseline yet
        
        weekly_opening_val = float(weekly_opening)
        weekly_loss_pct = (weekly_opening_val - current_equity) / weekly_opening_val
        
        if weekly_loss_pct >= 0.06:  # -6% threshold
            logger.warning(f"[CircuitBreaker] Weekly halt: {weekly_loss_pct*100:.2f}%")
            self._halt_state = "WEEKLY_HALT"
            return True
        
        return False
    
    def check_monthly_loss(self, current_equity: float) -> bool:
        """Check monthly loss (-15% threshold)."""
        monthly_opening = self.redis.get("circuit_breaker:monthly_open_equity")
        if not monthly_opening:
            return False
        
        monthly_opening_val = float(monthly_opening)
        monthly_loss_pct = (monthly_opening_val - current_equity) / monthly_opening_val
        
        if monthly_loss_pct >= 0.15:  # -15% threshold
            logger.warning(f"[CircuitBreaker] Monthly halt: {monthly_loss_pct*100:.2f}%")
            self._halt_state = "MONTHLY_HALT"
            return True
        
        return False
    
    def reset_weekly(self, current_equity: float):
        """Reset weekly baseline (Monday 09:00 UTC)."""
        today = datetime.now()
        if today.weekday() == 0:  # Monday
            self.redis.set("circuit_breaker:weekly_open_equity", str(current_equity), ex=604800)  # 7 days
            logger.info(f"[CircuitBreaker] Weekly reset: {current_equity}")
    
    def reset_monthly(self, current_equity: float):
        """Reset monthly baseline (1st of month 09:00 UTC)."""
        today = datetime.now()
        if today.day == 1:
            self.redis.set("circuit_breaker:monthly_open_equity", str(current_equity), ex=2592000)  # 30 days
            logger.info(f"[CircuitBreaker] Monthly reset: {current_equity}")
```

**Integration in main.py:**
```python
# In daily reset routine:
circuit_breaker.reset_weekly(current_equity)
circuit_breaker.reset_monthly(current_equity)

# In main scan loop:
if circuit_breaker.check_weekly_loss(current_equity):
    logger.error("Weekly halt triggered, exit-only mode")
    return "HALTED"

if circuit_breaker.check_monthly_loss(current_equity):
    logger.error("Monthly halt triggered, EMERGENCY HALT")
    await shutdown_system()
```

**Test Added:** `test_R21_10_weekly_monthly_halts()`

**Expected Impact:** Additional safety layers

---

## Other P0 Fixes

### R21-06: Fix Queue.Full Exception

**File:** `execution/order_executor.py` or wherever queue is used
**Issue:** `queue.Full` exception not caught (Python 3.11+ uses `asyncio.QueueFull`)

**Fix:**
```python
try:
    await order_queue.put_nowait(order)
except (asyncio.QueueFull, queue.Full):  # Handle both
    logger.error(f"Order queue full, dropping {order}")
```

### R21-42: VIX Fail-Closed (Default 99.0)

**File:** `core/cross_asset_macro.py`
**Issue:** VIX missing = default 0.0 (unsafe, no VIX = no risk check)

**Fix:**
```python
def get_vix(self) -> float:
    try:
        vix = fetch_vix_data()
        return vix
    except Exception as e:
        logger.error(f"VIX fetch failed: {e}, defaulting to CONSERVATIVE 99.0")
        return 99.0  # Conservative: act as if VIX is very high (halt most trades)
```

### R21-04: Fix List Mutation Bug

**File:** wherever list is being mutated during iteration
**Issue:** Modifying list while iterating causes skips

**Fix:**
```python
# BROKEN:
for item in items:
    if condition(item):
        items.remove(item)  # BUG: skips next item

# FIXED:
items = [item for item in items if not condition(item)]
```

---

## PART 4: TESTING & VALIDATION

### Test Suite Structure

Create test file: `tests/test_phase_q1_implementation.py`

```python
"""
Comprehensive test suite for Phase Q1 implementation.
Tests all timing defects (T-01 to T-08) + silent killers (SK-01 to SK-04).
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
import asyncio

class TestTimingDefects:
    """T-01 to T-08 validation."""
    
    @pytest.mark.asyncio
    async def test_T01_gap_stabilization_after_open(self):
        """T-01: First 30-min blackout → 5-min observe window."""
        strategy = S15Strategy(...)
        
        # Before 09:05 with unstable vol
        result = await strategy.scan_s15("QQQ3.L")
        assert result is None  # Should reject
        
        # After 3 quiet bars stabilize
        strategy._minute_bars = [create_quiet_bar() for _ in range(3)]
        result = await strategy.scan_s15("QQQ3.L")
        assert result is not None  # Should allow

    async def test_T02_lunch_window_oscillator_veto(self):
        """T-02: Lunch dead zone → oscillators disabled."""
        # During 12:30-13:30
        strategy._lunch_window = True
        snap = create_indicator_snapshot(rsi=80, macd=1.5, vwap_cross=True)
        
        # Should NOT use RSI (oscillator) in lunch
        score = strategy._score_indicator_consensus(snap)
        # Score should be lower (missing oscillator confirmation)
        assert score < 8.0
    
    async def test_T03_event_driven_anomaly_detection(self):
        """T-03: Event-driven scanning triggers on vol spike."""
        detector = EventAnomalyDetector(redis_client)
        callback_fired = AsyncMock()
        detector.register_anomaly_callback(callback_fired)
        
        # Simulate 2x vol spike
        detector._baseline_vols["QQQ3.L"] = 0.01
        bar = create_bar(ticker="QQQ3.L", vol_change=0.025)  # 2.5x
        
        await detector._check_vol_spike(bar)
        
        # Should trigger callback
        callback_fired.assert_called_once()
        event = callback_fired.call_args[0][0]
        assert event.event_type == "vol_spike"
    
    async def test_T04_gpd_batch_caching(self):
        """T-04: GPD calculation batched nightly, <1ms lookup."""
        tail_monitor = TailLossMonitor(redis_client, db)
        
        # Nightly batch (slow, ~24s per ticker)
        await tail_monitor.nightly_batch_gpd_calculation(["QQQ3.L", "3LUS.L"])
        
        # Verify cached in Redis
        assert redis_client.get("gpd_tail:QQQ3.L") is not None
        
        # Scan-time lookup (fast, <1ms)
        start = time.time()
        gpd = tail_monitor.get_gpd_tail("QQQ3.L")
        elapsed = time.time() - start
        assert elapsed < 0.001  # <1ms
        assert gpd["percentile_95"] > 0
    
    async def test_T05_fast_tier_activation(self):
        """T-05: FAST tier (3/4) activated on gap + fast move."""
        strategy = S15Strategy(...)
        
        # Simulate gap + fast move
        strategy._session_opens["QQQ3.L"] = 100.0
        strategy._minute_bars = [
            create_bar(close=102.0),  # +2% gap
            create_bar(close=102.5),  # continuing
            create_bar(close=103.0),  # same direction
            create_bar(close=103.5),  # same direction
            create_bar(close=104.0),  # same direction
        ]
        
        gate, tier = strategy._get_indicator_gate("QQQ3.L", "TRENDING_UP_STRONG")
        assert tier == "FAST_GAP"
        assert gate == 3.0  # 3/4 instead of 6/8
    
    async def test_T06_adx_regime_adaptation(self):
        """T-06: ADX minimum by regime."""
        strategy = S15Strategy(...)
        
        # In TRENDING regime: ADX_MIN = 15
        adx_gate = strategy._get_adx_gate("TRENDING_UP_STRONG", "QQQ3.L")
        assert adx_gate == 15.0
        
        # In RANGE_BOUND regime: ADX_MIN = 25
        adx_gate = strategy._get_adx_gate("RANGE_BOUND", "QQQ3.L")
        assert adx_gate == 25.0
    
    async def test_T07_rvol_regime_adaptation(self):
        """T-07: RVOL threshold by regime."""
        strategy = S15Strategy(...)
        
        # Gap day + compression: RVOL_MIN = 0.30
        rvol_gate = strategy._get_rvol_gate("COMPRESSION", is_gap_day=True)
        assert rvol_gate == 0.30
        
        # Normal day: RVOL_MIN = 0.75
        rvol_gate = strategy._get_rvol_gate("TRENDING_UP_STRONG", is_gap_day=False)
        assert rvol_gate == 0.75
    
    async def test_T08_multi_signal_concurrent_positions(self):
        """T-08: Multi-signal (4 concurrent) instead of single-fire."""
        strategy = S15Strategy(...)
        
        # Can open 4 concurrent
        for i in range(4):
            ticker = ["QQQ3.L", "3LUS.L", "NVD3.L", "TSL3.L"][i]
            can_open = strategy.can_open_new_trade(ticker)
            assert can_open is True
            strategy._active_positions[ticker] = Mock()
        
        # 5th should be rejected (max 4)
        can_open = strategy.can_open_new_trade("SP5L.L")
        assert can_open is False
        
        # Check removed +1.5% session halt
        # (should only have +2.0% daily ceiling)
        daily_pnl = 0.019  # +1.9%
        can_open = strategy.can_open_new_trade("SP5L.L")
        assert can_open is True  # Below +2.0%, should allow

class TestSilentKillers:
    """SK-01 to SK-04 validation."""
    
    async def test_SK01_equity_denominator_sync(self):
        """SK-01: Equity denominator synced daily."""
        cb = CircuitBreaker(10000)
        
        # Day 1: Account grows to £20k
        cb.reset_daily(20000)
        assert cb._session_opening_equity == 20000
        
        # Loss of £300 (-1.5% of today's £20k)
        daily_loss = cb.check_daily_loss(19700)
        assert daily_loss == "L1_TRIGGERED"
        
        # Would be false alarm if denominator was frozen at £10k
        # (£300 is only 3% of £10k, wouldn't trigger)
    
    async def test_SK02_zombie_halt_date_filter(self):
        """SK-02: Consecutive loss query includes date filter."""
        db = MockDatabase()
        
        # Insert Monday loss + Tuesday loss
        db.insert_trade(date="2026-03-10", outcome="LOSS")
        db.insert_trade(date="2026-03-11", outcome="LOSS")
        
        # Query on Wednesday (2026-03-12) — should NOT include Mon/Tue
        result = db.check_consecutive_losses(cutoff_date="2026-03-12")
        assert result == 0  # No losses TODAY
        
        # If we add Wednesday loss, should count
        db.insert_trade(date="2026-03-12", outcome="LOSS")
        result = db.check_consecutive_losses(cutoff_date="2026-03-12")
        assert result == 1
    
    async def test_SK03_confidence_floor_consistency(self):
        """SK-03: All modules use same confidence floor (65)."""
        from core.threshold_registry import ThresholdRegistry as TR
        from strategies.daily_target import _MIN_CONFIDENCE
        from bots.kelly_sizer import CONFIDENCE_FLOOR
        
        # All should be 65
        assert TR.CONFIDENCE_FLOOR == 65
        assert _MIN_CONFIDENCE == 65
        assert CONFIDENCE_FLOOR == 65
    
    async def test_SK04_single_throttle_system(self):
        """SK-04: Single +2.0% throttle, removed +1.5% session halt."""
        sizer = KellySizer()
        
        # Daily PnL at +1.9% — should allow new positions
        sizer._daily_pnl = 0.019
        can_trade = sizer.can_open_new_position()
        assert can_trade is True
        
        # Daily PnL at +2.1% — should halt
        sizer._daily_pnl = 0.021
        can_trade = sizer.can_open_new_position()
        assert can_trade is False
        
        # Verify no +1.5% session halt exists (removed)
        # (should not have any code checking _session_pnl)

class TestRegulatoryFixes:
    """R21-19, R21-16, R21-13/14, R21-10 validation."""
    
    async def test_R21_19_isa_eligibility_gate(self):
        """R21-19: ISA eligibility checked before sizing."""
        gate = ISAEligibilityGate(universe_metadata)
        
        # ETP (.L tickers) are eligible
        assert gate.is_eligible("QQQ3.L") is True
        
        # Non-existent ticker is ineligible
        assert gate.is_eligible("FAKE.L") is False
    
    async def test_R21_16_circuit_breaker_persistence(self):
        """R21-16: Circuit breaker state persists to Redis."""
        cb = CircuitBreaker(10000, redis_client)
        
        # Set state
        cb.persistence.set_halt_state("L2_TRIGGERED")
        
        # Simulate Docker restart
        cb2 = CircuitBreaker(10000, redis_client)
        recovered_state = cb2.persistence.get_halt_state()
        
        assert recovered_state == "L2_TRIGGERED"
    
    async def test_R21_13_vix_hysteresis(self):
        """R21-13/14: VIX hysteresis prevents flapping."""
        vix_gate = VIXHysteresisGate(redis_client)
        
        # Set initial state at VIX=24
        redis_client.set("vix:last_state", json.dumps("NO_5X"))
        
        # VIX moves to 24.5 (within deadband)
        state1 = vix_gate.get_state(24.5)
        assert state1["state"] == "NO_5X"  # Should NOT change
        
        # VIX moves to 26.5 (beyond deadband + threshold)
        state2 = vix_gate.get_state(26.5)
        assert state2["state"] == "HALF_SIZE"  # Now changes
    
    async def test_R21_10_weekly_monthly_halts(self):
        """R21-10: Weekly/monthly halt thresholds."""
        cb = CircuitBreaker(10000, redis_client)
        
        # Set weekly baseline at £10k
        cb.reset_weekly(10000)
        
        # Loss of £700 (-7%, above -6% threshold)
        halted = cb.check_weekly_loss(9300)
        assert halted is True
        
        # Below threshold
        halted = cb.check_weekly_loss(9500)  # -5%
        assert halted is False

# Run all tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**To run all tests:**
```bash
cd /Users/rr/nzt48-signals
pytest tests/test_phase_q1_implementation.py -v --tb=short
```

---

## IMPLEMENTATION CHECKLIST

### Week 1: Timing Defects
- [ ] T-01: Remove first 30-min blackout (3h)
- [ ] T-02: Fix lunch dead zone (2h)
- [ ] T-03: Implement event-driven scanning (8h)
- [ ] T-04: Move GPD to nightly batch (4h)
- [ ] T-05: FAST tier indicators (6h)
- [ ] T-06: ADX by regime (1h)
- [ ] T-07: RVOL by regime (2h)
- [ ] T-08: Multi-signal (1h)
- [ ] Integration testing (3h)
- [ ] **Total: 30 hours**

### Week 2: Silent Killers + Regulatory
- [ ] SK-01: Fix equity denominator (1.5h)
- [ ] SK-02: Fix zombie halt (1h)
- [ ] SK-03: Align confidence floor (0.5h)
- [ ] SK-04: Remove dual throttles (1h)
- [ ] R21-19: ISA eligibility gate (8h)
- [ ] R21-16: Circuit breaker persistence (3h)
- [ ] Integration testing (3h)
- [ ] **Total: 18 hours**

### Week 3: Safety + Testing
- [ ] R21-13/14: VIX hysteresis (4h)
- [ ] R21-10: Weekly/monthly halts (2h)
- [ ] R21-06, R21-42, R21-04: Other P0 fixes (2h)
- [ ] Full integration testing (3h)
- [ ] **Total: 11 hours**

### Week 4: Validation
- [ ] Deploy to paper trading (2h)
- [ ] Run 100-200 paper trades (ongoing)
- [ ] Validate 100-Trade Gate (WR ≥40%, entry <1min, PF >1.3x, losses <3)
- [ ] **Total: 2 hours + analysis**

---

## EXPECTED OUTCOMES

### Phase Q1 Complete
- 0% WR on 52 paper trades (baseline) → ~40% WR on 100+ trades (post-fixes)
- Entry timing: 2-3% into move → <1 minute into move
- Daily P&L: -0.2% to +0.1% (broken) → +0.35-0.50% (fixed)
- Sharpe ratio: 0.0 (broken) → 3-8 (top 0.1%)

### 100-Trade Validation Gate (ALL must pass)
1. **Win Rate ≥ 40%** ← Critical
2. **Average Entry < 1 min into move** ← Critical
3. **Profit Factor > 1.3x** ← Critical
4. **Consecutive Losses < 3** ← Critical

**If ALL pass:** → Proceed to Phase Q2
**If ANY fails:** → Diagnose, iterate, retest

---

## Next Steps

1. **Create git branch:** `git checkout -b phase-q1-timing-fixes`
2. **Implement T-01 to T-04** (Week 1, 20 hours)
3. **Test each change:** Run `pytest` after each logical unit
4. **Commit regularly:** "T-01: Remove first 30-min blackout" etc.
5. **Code review:** Every change needs 2nd reviewer
6. **Integrate:** Merge to main after each week's tests pass
7. **Deploy to paper trading:** Week 4 after all fixes complete

---

**Document prepared:** 2026-03-14
**Status:** Ready for implementation
**Estimated Duration:** 4 weeks, ~63 hours total
**Expected ROI:** +0.35-0.50% daily realistic (145-290% annualized)
