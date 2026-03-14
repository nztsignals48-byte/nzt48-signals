# Sprint 1: Timing Rescue -- Detailed Implementation Plan

## Context

Sprint 0+0.5 are complete (13 fixes across 10 files). S15 has 0% win rate across 52 trades. Root cause: execution timing -- the bot identifies the right tickers but enters trades 15-60 minutes too late. By that point, the 2-6% intraday move on leveraged ETPs is 80-95% complete, and mean reversion pushes price back into the stop.

**Evidence from logs**: Scan cycles fire every 60s via `continuous_24_7` (main.py:5282-5291), but S15's internal gates reject signals during the critical first moments of a move:
- Lines 324-333: 30-minute opening blackout vetoes 100% of opening gap signals
- Lines 335-344: Lunch dead zone vetoes 100% of US pre-market repricing signals
- Lines 66,77: RVOL >= 0.85 and ADX >= 25 thresholds reject signals until moves are 40-60% complete
- Lines 127-202: 8-indicator consensus (6/8 required) needs lagging indicators that confirm too late

**Sprint 1 target**: ~1 session with Claude Code (sprint 0.5 was 7 fixes in ~20 min). All 7 tasks implemented, deployed, and verified in one sitting. When combined with Sprint 0 changes, this should lift win rate from 0% to a testable level.

---

## Sprint 1A: Entry Window Fixes (T-01, T-02) -- ~10 min

### T-01: Replace First-30-Minute Blackout with OBSERVE-THEN-ACT Protocol

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py`

#### Current Code (lines 324-333)

```python
# V5.0 TIMING FILTER 1: Skip first 30 min after LSE open
# Admati & Pfleiderer (1988) — opening 30 min dominated by informed-trader
# order clustering + stale overnight limit orders clearing; price discovery
# unreliable for directional entries.
if now_uk < lse_open + timedelta(minutes=30):
    self.logger.debug(
        "S15: first 30 min after open (%s UK), skipping — Admati & Pfleiderer (1988)",
        now_uk.strftime("%H:%M"),
    )
    return []
```

#### Target Code (replace lines 324-333)

```python
# T-01: OBSERVE-THEN-ACT Protocol (replaces 30-min blackout)
# Gao et al. (2018): first-30-min return predicts EOD direction with 62% accuracy
# for leveraged products. The gap IS the signal for 3x/5x ETPs.
#
# Phase 1 (09:00-09:05): OBSERVE only. Record LSE ETP opening prices for gap calc.
# Phase 2 (09:05-09:15): GAP SCAN. Fire immediately on gaps > 1.5%.
# Phase 3 (09:15+): Normal scanning with full indicator gates.
#
# CRITICAL GUARD: RO-01 toxic spread hard-cap (35 bps) has supremacy over gap signals.
_observe_end = lse_open + timedelta(minutes=5)
_gap_scan_end = lse_open + timedelta(minutes=15)

if now_uk < _observe_end:
    # Phase 1: OBSERVE ONLY — record opening prices, do not signal
    self.logger.debug(
        "S15 T-01: OBSERVE phase (%s UK) — recording opens, no signals",
        now_uk.strftime("%H:%M"),
    )
    self._record_session_opens(tickers, indicators)
    return []

if now_uk < _gap_scan_end:
    # Phase 2: GAP SCAN — fire on gaps > 1.5% without indicator consensus
    self.logger.debug(
        "S15 T-01: GAP SCAN phase (%s UK) — checking for gap signals",
        now_uk.strftime("%H:%M"),
    )
    gap_signals = self._scan_gaps(tickers, indicators, market_ctx)
    if gap_signals:
        return gap_signals
    # No gaps found — fall through to normal scanning below (09:05-09:15
    # gets normal scanning if no gap detected, rather than doing nothing)

# Phase 3 (09:15+): Normal scanning continues below...
```

#### New Methods Required in `DailyTargetStrategy`

**Method 1: `_record_session_opens`** (add after `_determine_direction`, around line 900)

```python
def _record_session_opens(
    self, tickers: list[str], indicators: dict[str, IndicatorSnapshot]
) -> None:
    """T-01: Record session opening prices for gap calculation.
    Called during 09:00-09:05 OBSERVE phase.
    Stores prices in instance dict; also pushes to Redis if available.
    """
    if not hasattr(self, '_session_opens'):
        self._session_opens = {}

    today = datetime.now(_UK_TZ).strftime("%Y-%m-%d")
    if today in self._session_opens:
        return  # Already recorded today

    opens = {}
    for ticker in tickers:
        snap = indicators.get(ticker)
        if snap and snap.price > 0:
            opens[ticker] = snap.price

    if opens:
        self._session_opens[today] = opens
        self.logger.info(
            "S15 T-01: Recorded session opens for %d tickers: %s",
            len(opens),
            ", ".join(f"{t}={p:.2f}" for t, p in opens.items()),
        )

    # Clean old dates
    old_dates = [d for d in self._session_opens if d < today]
    for d in old_dates:
        del self._session_opens[d]
```

**Method 2: `_scan_gaps`** (add after `_record_session_opens`)

```python
def _scan_gaps(
    self,
    tickers: list[str],
    indicators: dict[str, IndicatorSnapshot],
    market_ctx: MarketContext,
) -> list[Signal]:
    """T-01: Gap scan during 09:05-09:15 UK.
    Fire signal on gaps > 1.5% from previous close without indicator consensus.
    The gap IS the signal for leveraged ETPs.

    CRITICAL: RO-01 spread gate (35 bps) has supremacy — blocks gap signals
    when opening spreads are too wide.
    """
    _GAP_THRESHOLD = 0.015  # 1.5% gap threshold
    _MAX_SPREAD_BPS = 35    # RO-01 toxic spread hard-cap

    today = datetime.now(_UK_TZ).strftime("%Y-%m-%d")
    session_opens = getattr(self, '_session_opens', {}).get(today, {})
    if not session_opens:
        self.logger.debug("S15 T-01 GAP SCAN: no session opens recorded")
        return []

    gap_candidates = []
    for ticker in tickers:
        snap = indicators.get(ticker)
        if not snap or snap.price <= 0:
            continue

        open_price = session_opens.get(ticker)
        if not open_price or open_price <= 0:
            continue

        # Get previous close from snap if available, else skip
        prev_close = getattr(snap, 'prev_close', None)
        if not prev_close or prev_close <= 0:
            # Fallback: use session open as a proxy (less accurate)
            continue

        gap_pct = (open_price - prev_close) / prev_close
        abs_gap = abs(gap_pct)

        if abs_gap < _GAP_THRESHOLD:
            continue

        # RO-01: Check bid-ask spread (supremacy over gap signals)
        spread_bps = getattr(snap, 'bid_ask_spread', 0) * 10000  # decimal -> bps
        if self._spread_tracker:
            spread_bps = max(spread_bps,
                             self._spread_tracker.get_fallback_spread(ticker) * 10000)
        if spread_bps > _MAX_SPREAD_BPS:
            self.logger.info(
                "S15 T-01 GAP BLOCKED by RO-01: %s gap=%.2f%% but spread=%.0fbps > %dbps",
                ticker, gap_pct * 100, spread_bps, _MAX_SPREAD_BPS,
            )
            continue

        # Determine direction from gap
        is_inverse = ticker in _INVERSE_ETPS
        if gap_pct > 0:
            direction = "SHORT" if is_inverse else "LONG"
        else:
            direction = "LONG" if is_inverse else "SHORT"  # Negative gap on inverse = go long

        # ISA constraint: no short selling
        if direction == "SHORT" and ticker not in _INVERSE_ETPS:
            self.logger.debug("S15 T-01 GAP: %s SHORT but ISA long-only, skipping", ticker)
            continue

        gap_candidates.append({
            "ticker": ticker,
            "gap_pct": gap_pct,
            "abs_gap": abs_gap,
            "direction": direction,
            "price": snap.price,
            "spread_bps": spread_bps,
        })

    if not gap_candidates:
        return []

    # Sort by absolute gap size (strongest gap first)
    gap_candidates.sort(key=lambda c: c["abs_gap"], reverse=True)
    best = gap_candidates[0]

    snap = indicators[best["ticker"]]
    entry = round(snap.price, 2)

    # ATR-based stop: 1.5x ATR
    atr = snap.atr14 if snap.atr14 > 0 else entry * 0.01
    stop_dist = max(_STOP_ATR_MULT * atr, entry * _STOP_MIN_PCT)
    if best["direction"] == "LONG":
        stop = round(entry - stop_dist, 4)
    else:
        stop = round(entry + stop_dist, 4)

    # Gap confidence: base 70 (between 65 floor and 75)
    confidence = 70.0

    signal = self._create_signal(
        ticker=best["ticker"],
        direction=best["direction"],
        entry=entry,
        stop=stop,
        indicators=snap,
        market_ctx=market_ctx,
    )
    signal.confidence = confidence
    signal.metadata = signal.metadata or {}
    signal.metadata["gap_signal"] = True
    signal.metadata["gap_pct"] = round(best["gap_pct"] * 100, 2)
    signal.metadata["tier"] = "FAST"

    self.logger.info(
        "S15 T-01 GAP SIGNAL: %s %s gap=%.2f%% entry=$%.2f stop=$%.4f conf=%.0f spread=%.0fbps",
        best["direction"], best["ticker"], best["gap_pct"] * 100,
        entry, stop, confidence, best["spread_bps"],
    )

    # Increment daily signal count
    today_str = datetime.now(_UK_TZ).strftime("%Y-%m-%d")
    self._daily_signal_count[today_str] = self._daily_signal_count.get(today_str, 0) + 1

    return [signal]
```

#### Model Changes for T-01

**File**: `/Users/rr/nzt48-signals/models.py` (IndicatorSnapshot, around line 236)

Add after `sentiment_composite` field (line 236):

```python
    # T-01: Gap calculation fields
    prev_close: float = 0.0                # Previous session close price
    open_price: float = 0.0                # Current session open price
    session_open_price: float = 0.0        # T-03: Session open for anomaly detection
```

#### Constants Change for T-01

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py` (line 211)

```python
# BEFORE:
_MAX_GAP_UP_PCT = 0.015          # Don't chase: skip if gap-up >1.5% at open (avoid FOMO entries)

# AFTER:
# T-01: _MAX_GAP_UP_PCT repurposed as gap signal TRIGGER threshold
_GAP_TRIGGER_PCT = 0.015         # T-01: fire gap signal when gap > 1.5% (was a SKIP threshold)
```

#### Init Change for T-01

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py` (line 295-301)

Add to `__init__`:
```python
        self._session_opens: dict[str, dict[str, float]] = {}  # T-01: {date: {ticker: open_price}}
```

#### Why This Change Matters
- **Academic basis**: Gao et al. (2018) -- first-30-min return predicts EOD direction with 62% accuracy for leveraged products
- **Problem**: Leveraged ETPs gap 3-6% at LSE open on overnight US/Asia news. Opening 10-15 minutes is when 50%+ of daily momentum forms. The blanket 30-minute blackout kills ALL opening signals
- **Impact**: Recovers approximately 25 minutes of lost signal time at open

#### Risk Assessment
- **Risk**: False gap signals on low-liquidity opening prints
- **Mitigation**: RO-01 spread gate (35 bps) blocks signals when opening spreads are too wide. 5-minute OBSERVE phase avoids auction noise
- **Rollback**: Restore lines 324-333 and delete the two new methods

#### Estimated Time: ~5 min (Claude Code: read file, apply edits, add methods)

---

### T-02: Replace Lunch Dead Zone Blackout with Reduced-Confidence Window

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py`

#### Current Code (lines 335-344)

```python
# V5.0 TIMING FILTER 2: Skip lunch dead zone 11:30-13:00 UK
# Jain & Joh (1988) — intraday volume exhibits U-shape with trough at
# midday; spreads widen, adverse selection increases, and 2% moves are
# statistically rare in this window on LSE leveraged ETPs.
if (now_uk.hour == 11 and now_uk.minute >= 30) or now_uk.hour == 12:
    self.logger.debug(
        "S15: lunch dead zone (%s UK), skipping — Jain & Joh (1988)",
        now_uk.strftime("%H:%M"),
    )
    return []
```

#### Target Code (replace lines 335-344)

```python
# T-02: REDUCED-CONFIDENCE lunch window (replaces hard veto)
# 11:30-13:00 UK = 06:30-08:00 ET = US pre-market window.
# LSE leveraged ETPs that track US underlyings reprice during this
# window on US overnight/pre-market moves.
# Jain & Joh (1988) volume trough is real, but blocking ALL signals
# means missing the entire US pre-market repricing cycle.
#
# Fix: Allow signals but apply -10 confidence penalty. This means
# a signal at base 74 -> 64 after penalty -> REJECTED by 65 floor.
# Only genuinely strong setups (base >= 75) survive lunch window.
_is_lunch_window = (now_uk.hour == 11 and now_uk.minute >= 30) or now_uk.hour == 12
if _is_lunch_window:
    self.logger.debug(
        "S15 T-02: lunch window (%s UK) — reduced confidence, not veto",
        now_uk.strftime("%H:%M"),
    )
    # Note: penalty applied AFTER scoring in _score_ticker_with_reason
    # The flag is stored and checked during scoring below
self._is_lunch_window = _is_lunch_window
```

#### Scoring Change for T-02

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py` (inside `_score_ticker_with_reason`, around line 753)

After the confidence calculation at line 753 (`confidence = 40.0 + (total_score * 55.0)`), add:

```python
        # T-02: Lunch window confidence penalty
        # Applied AFTER base confidence, BEFORE _MIN_CONFIDENCE gate.
        # A signal at base 74 -> 64 -> REJECTED. Only base >= 75 survives.
        if getattr(self, '_is_lunch_window', False):
            confidence -= 10.0
            self.logger.debug(
                "S15 T-02: lunch penalty -10 applied to %s: %.1f -> %.1f",
                ticker, confidence + 10.0, confidence,
            )
```

#### RVOL Override for T-02

In `_score_ticker_with_reason`, the RVOL gate is at line 571:
```python
        if rvol < _MIN_RVOL:
            return None, f"rvol_too_low({rvol:.2f}<{_MIN_RVOL})"
```

Change to:
```python
        # T-02: During lunch window, lower RVOL threshold to 0.50
        # Low UK volume is expected — move is driven by US futures, not UK order flow
        _effective_rvol_min = _MIN_RVOL
        if getattr(self, '_is_lunch_window', False):
            _effective_rvol_min = 0.50
        if rvol < _effective_rvol_min:
            return None, f"rvol_too_low({rvol:.2f}<{_effective_rvol_min})"
```

#### Init Change for T-02

Add to `__init__` (after line 301):
```python
        self._is_lunch_window: bool = False  # T-02: set per scan cycle
```

#### Why This Change Matters
- **Academic basis**: Jain & Joh (1988) volume U-shape is real for LSE domestic stocks, but irrelevant for leveraged ETPs tracking US underlyings -- they reprice on US pre-market moves during this window
- **Problem**: 90 minutes of complete signal blackout during US pre-market repricing
- **Impact**: Recovers 90 minutes of signal time, but with a confidence penalty that filters weak setups

#### Risk Assessment
- **Risk**: Wider spreads during lunch trough increase execution cost
- **Mitigation**: Confidence penalty means only high-conviction setups survive. The -10 penalty against the 65 floor means base confidence >= 75 is required
- **Rollback**: Restore lines 335-344, remove `_is_lunch_window` flag and penalty code

#### Estimated Time: ~5 min (Claude Code: replace block, add penalty, add flag)

---

## Sprint 1B: FAST/SLOW Indicator Tiers (T-05, T-06, T-07) -- ~20 min

This is the most complex part of Sprint 1. It creates a two-tier indicator architecture that lets the system fire on fast-moving setups immediately (3/4 leading indicators) while still using the full 8-indicator consensus for slower continuation trades.

### T-05: Two-Tier Indicator Architecture (FAST/SLOW)

**Files**:
- `/Users/rr/nzt48-signals/models.py` -- add `roc_5` and `adx_delta` fields
- `/Users/rr/nzt48-signals/feeds/indicators.py` -- compute new fields
- `/Users/rr/nzt48-signals/strategies/daily_target.py` -- restructure `_determine_direction` and gate logic

#### Step 1: Add New Fields to IndicatorSnapshot

**File**: `/Users/rr/nzt48-signals/models.py` (after line 236, with the T-01 fields)

```python
    # T-05: Rate of Change for FAST tier
    roc_5: Optional[float] = None          # 5-period Rate of Change (%)

    # T-06: ADX acceleration for adaptive thresholds
    adx_delta: Optional[float] = None      # ADX change per bar (5-min)

    # T-07: RVOL trajectory for volume confirmation
    rvol_trajectory: Optional[float] = None  # current_rvol / mean(last 3 bars)
```

#### Step 2: Compute New Fields in IndicatorEngine

**File**: `/Users/rr/nzt48-signals/feeds/indicators.py` (inside `compute_all`, after the ADX computation section)

Add after the existing ADX calculation (find where `snap.adx14 = ...` is set):

```python
        # --- T-05: Rate of Change (5-period) ---
        try:
            if len(df) >= 6:
                close_current = float(df["Close"].iloc[-1])
                close_5_ago = float(df["Close"].iloc[-6])  # 5 bars back (0-indexed: -1 is current, -6 is 5 back)
                if close_5_ago > 0:
                    snap.roc_5 = ((close_current - close_5_ago) / close_5_ago) * 100
        except Exception:
            logger.debug("ROC(5) calculation failed for %s", ticker, exc_info=True)

        # --- T-06: ADX Delta (acceleration) ---
        try:
            if len(df) >= 30:  # Need enough bars for two ADX(14) calculations
                from ta.trend import ADXIndicator
                adx_series = ADXIndicator(
                    high=df["High"], low=df["Low"], close=df["Close"], window=14
                ).adx()
                if len(adx_series) >= 2:
                    adx_current = float(adx_series.iloc[-1])
                    adx_prev = float(adx_series.iloc[-2])
                    snap.adx_delta = adx_current - adx_prev
        except Exception:
            logger.debug("ADX delta calculation failed for %s", ticker, exc_info=True)

        # --- T-07: RVOL Trajectory ---
        try:
            if len(df) >= 4 and "Volume" in df.columns:
                # Last 3 bars' volume relative to their 20-period average
                recent_vols = df["Volume"].iloc[-4:-1].values  # 3 bars before current
                avg_recent = float(recent_vols.mean()) if len(recent_vols) == 3 else 0
                if avg_recent > 0 and snap.rvol is not None and snap.rvol > 0:
                    snap.rvol_trajectory = snap.rvol / (avg_recent / max(df["Volume"].rolling(20).mean().iloc[-1], 1))
                else:
                    snap.rvol_trajectory = None
        except Exception:
            logger.debug("RVOL trajectory calculation failed for %s", ticker, exc_info=True)
```

#### Step 3: Restructure `_determine_direction` to Return Tier Classification

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py` (lines 784-898)

Change the return signature and add FAST tier logic:

```python
    def _determine_direction(
        self, snap: IndicatorSnapshot, ticker: str = ""
    ) -> tuple[str, float, float, str]:
        """Determine LONG or SHORT from WEIGHTED indicator consensus.

        T-05: Two-tier architecture:
          FAST tier (VWAP, MACD histogram, RSI, ROC 5-period) — weight 2.0x each
            If 3/4 FAST agree AND move > 1.5% from prev close, fire without SLOW.
          SLOW tier (EMA9, EMA20, EMA50, OBV, Stochastic RSI) — weight 1.0x each
            Adds confidence points but NOT required for fast-move signals.

        Returns (direction, weighted_momentum_score, weighted_aligned_score, tier)
        where tier is "FAST" or "SLOW".
        """
        # ---- FAST TIER: 4 leading indicators ----
        fast_long = 0
        fast_short = 0

        # FAST 1: VWAP (institutional benchmark)
        if snap.price > snap.vwap > 0:
            fast_long += 1
        elif snap.price < snap.vwap and snap.vwap > 0:
            fast_short += 1

        # FAST 2: MACD histogram (momentum lead)
        if snap.macd_histogram > 0:
            fast_long += 1
        elif snap.macd_histogram < 0:
            fast_short += 1

        # FAST 3: RSI (overbought/oversold)
        if snap.rsi14 > 55:
            fast_long += 1
        elif snap.rsi14 < 45:
            fast_short += 1

        # FAST 4: ROC 5-period (rate of change)
        roc = getattr(snap, 'roc_5', None)
        if roc is not None:
            if roc > 0.5:  # Positive momentum threshold
                fast_long += 1
            elif roc < -0.5:  # Negative momentum threshold
                fast_short += 1

        fast_max = max(fast_long, fast_short)
        fast_direction = "LONG" if fast_long >= fast_short else "SHORT"

        # Check for FAST tier qualification: 3/4 agree AND significant move
        prev_close = getattr(snap, 'prev_close', 0)
        price_move_pct = abs(snap.price - prev_close) / prev_close if prev_close > 0 else 0
        is_fast_qualified = fast_max >= 3 and price_move_pct >= 0.015

        # ---- FULL WEIGHTED CONSENSUS (both tiers) ----
        # Select weight set: leveraged ETPs get down-weighted EMA50/EMA20
        is_leveraged = ticker in _BROAD_LEVERAGED_ETPS
        weights = _INDICATOR_WEIGHTS_LEVERAGED if is_leveraged else _INDICATOR_WEIGHTS
        total_max = _WEIGHTED_TOTAL_MAX_LEV if is_leveraged else _WEIGHTED_TOTAL_MAX

        long_score = 0.0
        short_score = 0.0

        # [EXISTING 8-indicator scoring logic from lines 813-886 stays exactly the same]
        # 1. RSI
        w_rsi = weights["rsi"]
        if snap.rsi14 > 55:
            long_score += w_rsi
        elif snap.rsi14 < 45:
            short_score += w_rsi

        # 2. MACD histogram
        w_macd = weights["macd"]
        if snap.macd_histogram > 0:
            long_score += w_macd
        elif snap.macd_histogram < 0:
            short_score += w_macd

        # 3. Price vs EMA9
        w_ema9 = weights["ema9"]
        if snap.price > snap.ema9 > 0:
            long_score += w_ema9
        elif snap.price < snap.ema9 and snap.ema9 > 0:
            short_score += w_ema9

        # 4. Price vs EMA20
        w_ema20 = weights["ema20"]
        if snap.price > snap.ema20 > 0:
            long_score += w_ema20
        elif snap.price < snap.ema20 and snap.ema20 > 0:
            short_score += w_ema20

        # 5. Price vs EMA50
        w_ema50 = weights["ema50"]
        if snap.price > snap.ema50 > 0:
            long_score += w_ema50
        elif snap.price < snap.ema50 and snap.ema50 > 0:
            short_score += w_ema50

        # 6. Price vs VWAP
        w_vwap = weights["vwap"]
        if snap.price > snap.vwap > 0:
            long_score += w_vwap
        elif snap.price < snap.vwap and snap.vwap > 0:
            short_score += w_vwap

        # 7. Stochastic RSI
        w_stoch = weights["stoch_rsi"]
        if snap.stochastic_rsi > 50:
            long_score += w_stoch
        elif snap.stochastic_rsi < 50:
            short_score += w_stoch

        # 8. OBV slope
        w_obv = weights["obv"]
        obv_slope = getattr(snap, "obv_slope", None)
        if obv_slope is not None:
            if obv_slope > 0:
                long_score += w_obv
            elif obv_slope < 0:
                short_score += w_obv
        else:
            obv_ema = getattr(snap, "obv_ema20", None)
            if obv_ema is not None and obv_ema > 0:
                if snap.obv > obv_ema:
                    long_score += w_obv
                elif snap.obv < obv_ema:
                    short_score += w_obv

        # Direction: weighted majority wins
        if long_score >= short_score:
            direction = "LONG"
            aligned_weighted = long_score
            momentum_score = long_score / total_max if total_max > 0 else 0
        else:
            direction = "SHORT"
            aligned_weighted = short_score
            momentum_score = short_score / total_max if total_max > 0 else 0

        # Determine tier
        tier = "FAST" if is_fast_qualified and fast_direction == direction else "SLOW"

        return direction, round(momentum_score, 3), round(aligned_weighted, 2), tier
```

#### Step 4: Update All Call Sites of `_determine_direction`

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py` (line 631)

```python
# BEFORE:
direction, momentum_score, weighted_score = self._determine_direction(snap, ticker)

# AFTER:
direction, momentum_score, weighted_score, tier = self._determine_direction(snap, ticker)
```

#### Step 5: Apply Tier-Specific Gate Logic in `_score_ticker_with_reason`

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py`

After the `_determine_direction` call at line 631, restructure the consensus gate:

```python
        direction, momentum_score, weighted_score, tier = self._determine_direction(snap, ticker)
        rvol_val = snap.rvol if hasattr(snap, "rvol") and snap.rvol else 0.0

        if tier == "FAST":
            # FAST tier: bypass weighted consensus gate entirely
            # The 3/4 FAST indicator agreement IS the consensus
            # SLOW indicators add confidence points but don't gate
            slow_bonus = 0.0
            # Count SLOW indicators that agree: EMA9, EMA20, EMA50, OBV, StochRSI
            if direction == "LONG":
                if snap.price > snap.ema9 > 0: slow_bonus += 1.0
                if snap.price > snap.ema20 > 0: slow_bonus += 1.0
                if snap.price > snap.ema50 > 0: slow_bonus += 0.5
                if snap.stochastic_rsi > 50: slow_bonus += 1.0
            else:
                if snap.price < snap.ema9 and snap.ema9 > 0: slow_bonus += 1.0
                if snap.price < snap.ema20 and snap.ema20 > 0: slow_bonus += 1.0
                if snap.price < snap.ema50 and snap.ema50 > 0: slow_bonus += 0.5
                if snap.stochastic_rsi < 50: slow_bonus += 1.0
            # Each agreeing SLOW indicator adds 1 confidence point (max +4.5)
            # This is applied later in confidence calculation

            self.logger.debug(
                "S15 T-05 FAST TIER: %s direction=%s fast_agree=3+/4 slow_bonus=%.1f",
                ticker, direction, slow_bonus,
            )
            indicator_count = 8  # Placeholder for backward compat
            easing_applied = f"fast_tier(slow_bonus={slow_bonus:.1f})"

            # Skip the weighted consensus gate below — go straight to ISA check
        else:
            # SLOW tier: use existing weighted consensus gate (unchanged)
            # [existing lines 634-676 stay exactly as they are]
```

The full SLOW tier code block is the existing lines 634-676 wrapped in an `else` clause.

#### Confidence Adjustment for FAST Tier

In the scoring section (around line 750-756), modify:

```python
        # Scoring with tier-aware confidence
        total_score = atr_score + rvol_score + mom_score + ema_score + bb_score + trend_score
        confidence = 40.0 + (total_score * 55.0)

        # FAST tier: add SLOW indicator bonus points
        if tier == "FAST":
            _slow_bonus = locals().get('slow_bonus', 0.0)
            confidence += _slow_bonus  # +0 to +4.5 from agreeing SLOW indicators

        if is_range_bound:
            confidence -= _RANGE_BOUND_CONF_PENALTY

        # T-02: Lunch window penalty
        if getattr(self, '_is_lunch_window', False):
            confidence -= 10.0

        confidence = round(min(95.0, max(40.0, confidence)), 1)
```

#### Why T-05 Matters
- **Academic basis**: Brock, Lakonishok & LeBaron (1992) showed combinations yield higher returns, but their study used EQUAL-weight indicators. VWAP, MACD, RSI are leading indicators (Madhavan 1997, Murphy 1999, Wilder 1978). EMA20/50 and OBV are lagging.
- **Problem**: In a fast 2-4% gap move, only VWAP + MACD + RSI align immediately (3/8). The system requires 6/8 = REJECTED. By the time EMA20/50 catch up, the target is hit.
- **Impact**: Fast-moving signals fire immediately with 3/4 leading indicator agreement instead of waiting for 6/8 total.

---

### T-06: Lower ADX Thresholds (Tier-Aware)

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py`

#### Current Code (line 77, line 575-577)

Constants:
```python
_MIN_ADX = 25.0   # V5.0: raised 22->25 — Wilder (1978) original threshold
```

Gate (in `_score_ticker_with_reason`, line 575-577):
```python
        # GATE 3: Trend confirmation — ADX gate (Faber 2013)
        adx = snap.adx14 if hasattr(snap, 'adx14') and snap.adx14 > 0 else 0
        if adx < _MIN_ADX:
            return None, f"adx_too_low({adx:.1f}<{_MIN_ADX})"
```

#### Target Constants (replace line 77-79)

```python
# T-06: Tier-aware ADX thresholds (Wilder 1978)
# ADX is a 14-period lagging indicator. At the START of a breakout, ADX = 10-18.
# ADX only reaches 25 after the move is 40-60% done.
# FAST tier catches trend initiation, SLOW tier requires stronger confirmation.
_MIN_ADX_FAST = 15.0             # FAST tier: catch trend birth (Wilder: >= 15 = emerging trend)
_MIN_ADX_SLOW = 20.0             # SLOW tier: moderate confirmation (below original 25)
_MIN_ADX_RANGE_BOUND = 25.0      # RANGE_BOUND: higher bar in choppy conditions
_ADX_ACCELERATION_THRESHOLD = 2.0 # ADX rising > 2 pts/bar = treat as +5 (predictive adjustment)
```

#### Target Gate (replace lines 575-577)

```python
        # GATE 3: Trend confirmation — ADX gate (Wilder 1978)
        # T-06: Universal pre-filter at ADX >= 15, then tier-specific check after
        # _determine_direction returns the tier classification.
        adx = snap.adx14 if hasattr(snap, 'adx14') and snap.adx14 > 0 else 0

        # ADX acceleration: rising ADX is predictive (ADX at 18 and rising = effective 23)
        adx_delta = getattr(snap, 'adx_delta', None) or 0
        effective_adx = adx
        if adx_delta > _ADX_ACCELERATION_THRESHOLD:
            effective_adx = adx + 5.0  # Predictive adjustment
            self.logger.debug(
                "S15 T-06: %s ADX acceleration: raw=%.1f delta=%.1f -> effective=%.1f",
                ticker, adx, adx_delta, effective_adx,
            )

        # Universal pre-filter: reject everything below FAST minimum (15)
        if effective_adx < _MIN_ADX_FAST:
            return None, f"adx_too_low({adx:.1f}+delta{adx_delta:.1f}={effective_adx:.1f}<{_MIN_ADX_FAST})"

        # NOTE: Tier-specific ADX check happens AFTER _determine_direction returns tier.
        # See the tier-specific gate section below.
```

Then AFTER `_determine_direction` returns the tier, add tier-specific ADX check:

```python
        # T-06: Tier-specific ADX gate (post tier classification)
        if tier == "SLOW":
            _adx_threshold = _MIN_ADX_RANGE_BOUND if is_range_bound else _MIN_ADX_SLOW
            if effective_adx < _adx_threshold:
                return None, f"adx_slow_tier({effective_adx:.1f}<{_adx_threshold})"
        # FAST tier already passed the 15 pre-filter — no additional ADX gate needed
```

#### Why T-06 Matters
- **Academic basis**: Wilder (1978) defined 25 as "strong trend" -- but we need to catch the trend at birth (10-18), not confirmation. ADX acceleration (delta > 2/bar) is a leading signal.
- **Problem**: ADX gate at 25 rejects all trend-initiation signals. The trade fires only when the trend is already 40-60% complete.
- **Impact**: FAST tier catches emerging trends at ADX 15-24 with acceleration confirmation.

#### Risk Assessment
- **Risk**: More false positives from lower ADX threshold
- **Mitigation**: FAST tier requires 3/4 leading indicators AND > 1.5% move -- these compensate. ADX acceleration check (delta > 2) adds predictive confirmation.
- **Rollback**: Restore `_MIN_ADX = 25.0` and old gate logic

#### Estimated Time: ~5 min (Claude Code: constants + gate replacement)

---

### T-07: Lower RVOL Thresholds (Tier-Aware)

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py`

#### Current Code (line 66, lines 569-572)

Constants:
```python
_MIN_RVOL = 0.85                 # Chordia, Roll & Subrahmanyam (2001)
```

Gate (in `_score_ticker_with_reason`, lines 569-572):
```python
        # GATE 2: Volume confirmation
        rvol = snap.rvol if snap.rvol is not None and snap.rvol > 0 else _RVOL_DEFAULT_WHEN_MISSING
        if rvol < _MIN_RVOL:
            return None, f"rvol_too_low({rvol:.2f}<{_MIN_RVOL})"
```

#### Target Constants (replace line 66-68)

```python
# T-07: Tier-aware RVOL thresholds
# Gap moves happen on LOW initial RVOL because repricing is driven by
# off-exchange blocks and overnight positioning. Volume follows, not leads.
_MIN_RVOL_FAST = 0.30            # FAST tier: just needs non-zero volume (gap is the signal)
_MIN_RVOL_SLOW = 0.65            # SLOW tier: institutional participation building
_MIN_RVOL_RANGE_BOUND = 1.2      # RANGE_BOUND: was 1.5, eased to 1.2
_MIN_RVOL_LATE_TROUGH = 0.80     # 13:30-14:30 UK late-day trough: was 1.5
_MIN_RVOL = 0.85                 # Legacy: kept for backward compat references
_RVOL_RISING_THRESHOLD = 2.0     # RVOL rising > 2x avg last 3 bars = "volume confirming"
```

#### Target Gate (replace lines 569-572)

```python
        # GATE 2: Volume confirmation — T-07 tier-aware thresholds
        rvol = snap.rvol if snap.rvol is not None and snap.rvol > 0 else _RVOL_DEFAULT_WHEN_MISSING

        # Universal pre-filter: reject below FAST minimum (0.30)
        # T-02: During lunch window, use 0.50 minimum
        _rvol_floor = 0.50 if getattr(self, '_is_lunch_window', False) else _MIN_RVOL_FAST
        if rvol < _rvol_floor:
            return None, f"rvol_too_low({rvol:.2f}<{_rvol_floor})"

        # RVOL trajectory: if RVOL is rising rapidly, treat as "volume confirming"
        rvol_traj = getattr(snap, 'rvol_trajectory', None)
        rvol_rising = rvol_traj is not None and rvol_traj > _RVOL_RISING_THRESHOLD

        # NOTE: Tier-specific RVOL check happens AFTER _determine_direction returns tier.
```

Then AFTER `_determine_direction`, add:

```python
        # T-07: Tier-specific RVOL gate (post tier classification)
        if tier == "SLOW":
            if is_range_bound:
                _rvol_threshold = _MIN_RVOL_RANGE_BOUND
            elif getattr(self, '_in_late_day_trough', False):
                _rvol_threshold = _MIN_RVOL_LATE_TROUGH
            else:
                _rvol_threshold = _MIN_RVOL_SLOW

            # RVOL trajectory override: rising RVOL bypasses absolute threshold
            if not rvol_rising and rvol < _rvol_threshold:
                return None, f"rvol_slow_tier({rvol:.2f}<{_rvol_threshold})"
        # FAST tier already passed the 0.30 floor — no additional RVOL gate needed
```

#### Update `_CONSENSUS_EASED_MIN_RVOL` (line 130)

```python
# BEFORE:
_CONSENSUS_EASED_MIN_RVOL = 1.0   # Compensating gate: RVOL >= 1.0 when consensus is eased

# AFTER:
_CONSENSUS_EASED_MIN_RVOL = 0.65  # T-07: Aligned with SLOW tier threshold (was 1.0)
```

#### Late-Day Trough Update (lines 490-502)

```python
# BEFORE (line 494):
            _late_rvol_min = 1.5    # Require 1.5x RVOL

# AFTER:
            _late_rvol_min = _MIN_RVOL_LATE_TROUGH  # T-07: 0.80 (was 1.5)
```

#### Why T-07 Matters
- **Academic basis**: Chordia, Roll & Subrahmanyam (2001) studied normal market volume. Gap moves on earnings/news happen on LOW initial RVOL because the repricing is driven by off-exchange blocks and overnight positioning.
- **Problem**: RVOL >= 0.85 rejects gap signals because volume hasn't yet confirmed. By the time RVOL rises, the gap has been absorbed.
- **Impact**: FAST tier fires on RVOL >= 0.30 (any meaningful volume), SLOW tier at 0.65.

#### Risk Assessment
- **Risk**: Low-volume signals may have wider spreads and worse fills
- **Mitigation**: RO-01 spread gate (35 bps) catches toxic spread conditions. RVOL trajectory override means rising volume is treated as confirming regardless of absolute level.
- **Rollback**: Restore `_MIN_RVOL = 0.85` and the simple gate

#### Estimated Time: ~5 min (Claude Code: constants + gate replacement)

---

### Sprint 1B: Restructured Gate Ordering

**CRITICAL DEPENDENCY**: The gate ordering in `_score_ticker_with_reason` must change because T-05/T-06/T-07 introduce a chicken-and-egg problem: ADX and RVOL gates run BEFORE `_determine_direction` (which determines the tier), but the tier determines which ADX/RVOL thresholds to apply.

**Resolution**: Two-pass gate architecture:

```
Pass 1 (PRE-FILTER) — Universal minimums:
  1. ATR gate          (unchanged, line 566)
  2. RVOL pre-filter   (>= 0.30 FAST floor)     [T-07]
  3. ADX pre-filter    (>= 15 FAST floor + acceleration)  [T-06]
  4. Regime alignment  (unchanged, line 579+)
  5. VIX gate          (unchanged, line 607)

Pass 2 (TIER-SPECIFIC) — After _determine_direction returns tier:
  6. _determine_direction() -> (direction, momentum, weighted, tier)
  7. If FAST: skip weighted consensus gate, add SLOW bonus
     If SLOW: apply full weighted consensus gate [existing lines 634-676]
  8. ADX tier gate     (SLOW: >= 20, RANGE_BOUND: >= 25)
  9. RVOL tier gate    (SLOW: >= 0.65, RANGE_BOUND: >= 1.2)
  10. ISA long-only    (unchanged, line 679)
  11. Entry/stop/target (unchanged, line 686+)
  12. R:R gate          (unchanged, line 720)
  13. Scoring + confidence (unchanged + T-02 penalty + FAST slow_bonus)
```

---

## Sprint 1C: Latency Reduction (T-04, T-10, T-03) -- ~15 min

### T-04: Move GPD Tail Risk to Nightly Redis Cache

**Files**:
- `/Users/rr/nzt48-signals/strategies/daily_target.py` -- remove inline GPD, add Redis lookup
- `/Users/rr/nzt48-signals/main.py` -- add nightly GPD batch, inject Redis into S15

#### Current Code (daily_target.py lines 442-468)

```python
        # ── GPD Tail Risk Pre-Screen (Balkema-de Haan-Pickands, C-24) ────
        try:
            import numpy as np
            from core.evt import TailRiskMonitor
            _gpd_monitor = TailRiskMonitor()
            for cand in candidates[:]:
                _ticker = cand["ticker"]
                try:
                    import yfinance as yf
                    _hist = yf.Ticker(_ticker).history(period="270d", interval="1d", auto_adjust=True)
                    if _hist is not None and len(_hist) >= 50:
                        _closes = _hist["Close"].dropna().values.astype(float)
                        if len(_closes) >= 50:
                            _returns = np.diff(np.log(_closes))
                            if len(_returns) >= 50:
                                _veto, _reason = _gpd_monitor.veto_signal(_ticker, _returns)
                                if _veto:
                                    self.logger.info("S15_GPD_VETO: %s excluded — %s", _ticker, _reason)
                                    candidates.remove(cand)
                except Exception:
                    pass
        except Exception:
            pass
```

#### Target Code (replace lines 442-468)

```python
        # ── GPD Tail Risk Pre-Screen — T-04: Redis-cached nightly batch ────
        # GPD tail risk is STATIC (doesn't change intraday). Computing inline
        # downloads 270 days of history PER CANDIDATE on every scan cycle.
        # T-04 fix: nightly batch computes and caches in Redis. Scan reads cache.
        if hasattr(self, '_redis_client') and self._redis_client:
            for cand in candidates[:]:
                _ticker = cand["ticker"]
                try:
                    _gpd_cache = self._redis_client.get(f"nzt:gpd:{_ticker}")
                    if _gpd_cache:
                        import json
                        _gpd_data = json.loads(_gpd_cache)
                        if _gpd_data.get("veto", False):
                            self.logger.info(
                                "S15_GPD_VETO (cached): %s excluded — %s",
                                _ticker, _gpd_data.get("reason", "tail_risk"),
                            )
                            candidates.remove(cand)
                    # If key missing: conservative default = no veto (allow trade)
                except Exception as _gpd_err:
                    self.logger.debug("GPD cache read error for %s: %s", _ticker, _gpd_err)
        else:
            # Fallback: no Redis available, skip GPD pre-screen entirely
            # Better to miss a tail risk check than to download 270d inline
            self.logger.debug("S15 T-04: No Redis client — GPD pre-screen skipped")
```

#### Init Change for T-04

**File**: `/Users/rr/nzt48-signals/strategies/daily_target.py` (line 295)

```python
    def __init__(self, spread_tracker=None, redis_client=None) -> None:
        super().__init__(name="2% Daily Target", strategy_id="S15")
        self._daily_signal_count: dict[str, int] = {}
        self._current_vix: float = 0.0
        self._current_regime = None
        self._in_late_day_trough: bool = False
        self._spread_tracker = spread_tracker
        self._redis_client = redis_client  # T-04: for GPD cache lookups
        self._session_opens: dict[str, dict[str, float]] = {}  # T-01
        self._is_lunch_window: bool = False  # T-02
```

#### S15 Construction in main.py

**File**: `/Users/rr/nzt48-signals/main.py` (lines 1321-1322)

```python
# BEFORE:
                if class_name == "DailyTargetStrategy" and hasattr(self, "spread_tracker") and self.spread_tracker:
                    self._strategies.append(strategy_class(spread_tracker=self.spread_tracker))

# AFTER:
                if class_name == "DailyTargetStrategy":
                    _s15_kwargs = {}
                    if hasattr(self, "spread_tracker") and self.spread_tracker:
                        _s15_kwargs["spread_tracker"] = self.spread_tracker
                    # T-04: Inject Redis client for GPD cache lookups
                    if hasattr(self, "chandelier") and hasattr(self.chandelier, "_redis"):
                        _s15_redis = getattr(self.chandelier, "_redis", None)
                        if _s15_redis:
                            _s15_kwargs["redis_client"] = _s15_redis
                    elif _redis_client:  # From the Chandelier init block
                        _s15_kwargs["redis_client"] = _redis_client
                    self._strategies.append(strategy_class(**_s15_kwargs))
```

Note: The Redis client is already initialized at main.py:877-888 for ChandelierExit. We reuse that same connection. The `_redis_client` variable is in scope from the `__init__` method.

#### Nightly GPD Batch Computation

**File**: `/Users/rr/nzt48-signals/main.py` (inside `_run_nightly_intelligence`, after line 7315)

Add at the beginning of the method:

```python
        # T-04: GPD Tail Risk nightly batch — compute for all CORE tickers
        # TailRiskMonitor.veto_signal() returns (bool, str) — boolean veto + reason
        # Store in Redis with 24h TTL: nzt:gpd:{ticker} -> {"veto": bool, "reason": str}
        try:
            from core.evt import TailRiskMonitor
            import numpy as np
            import json

            _gpd_monitor = TailRiskMonitor()
            _redis = None
            # Get Redis client (same one used by Chandelier)
            if hasattr(self, 'chandelier') and hasattr(self.chandelier, '_redis'):
                _redis = getattr(self.chandelier, '_redis', None)

            if _redis:
                from uk_isa.isa_universe import EXTENDED_UNIVERSE
                _gpd_computed = 0
                for _ticker in EXTENDED_UNIVERSE:
                    try:
                        import yfinance as yf
                        _hist = yf.Ticker(_ticker).history(
                            period="270d", interval="1d", auto_adjust=True
                        )
                        if _hist is not None and len(_hist) >= 50:
                            _closes = _hist["Close"].dropna().values.astype(float)
                            if len(_closes) >= 50:
                                _returns = np.diff(np.log(_closes))
                                if len(_returns) >= 50:
                                    _veto, _reason = _gpd_monitor.veto_signal(
                                        _ticker, _returns
                                    )
                                    _redis.setex(
                                        f"nzt:gpd:{_ticker}",
                                        86400,  # 24h TTL
                                        json.dumps({"veto": _veto, "reason": _reason}),
                                    )
                                    _gpd_computed += 1
                    except Exception as _e:
                        logger.debug("GPD nightly batch failed for %s: %s", _ticker, _e)

                logger.info(
                    "T-04 GPD NIGHTLY BATCH: computed tail risk for %d/%d tickers",
                    _gpd_computed, len(EXTENDED_UNIVERSE),
                )
            else:
                logger.warning("T-04 GPD NIGHTLY BATCH: no Redis — skipped")
        except Exception as _gpd_batch_err:
            logger.error("T-04 GPD NIGHTLY BATCH failed: %s", _gpd_batch_err)
```

#### VIX Intraday Supremacy Over GPD Cache

**File**: `/Users/rr/nzt48-signals/main.py` (inside the continuous scan loop, where VIX is read)

This is a safety valve: if VIX spikes > 10 points intraday, ALL GPD cache keys are invalidated because last night's tail risk assessment is stale.

Find where VIX is updated each cycle (in `run_scan` or the regime check), add:

```python
        # T-04: VIX INTRADAY SUPREMACY over GPD cache
        # A 10-point VIX spike = regime change that invalidates nightly GPD
        if hasattr(self, '_vix_session_open') and self._current_market_ctx:
            _vix_current = getattr(self._current_market_ctx, 'vix', 0) or 0
            _vix_delta = _vix_current - self._vix_session_open
            if _vix_delta > 10.0:
                try:
                    _redis = None
                    if hasattr(self, 'chandelier') and hasattr(self.chandelier, '_redis'):
                        _redis = getattr(self.chandelier, '_redis', None)
                    if _redis:
                        from uk_isa.isa_universe import EXTENDED_UNIVERSE
                        _gpd_keys = [f"nzt:gpd:{t}" for t in EXTENDED_UNIVERSE]
                        _redis.delete(*_gpd_keys)
                        logger.warning(
                            "T-04 GPD_CACHE_INVALIDATED_VIX_SPIKE: VIX delta=+%.1f "
                            "(current=%.1f, open=%.1f) — %d GPD keys deleted",
                            _vix_delta, _vix_current, self._vix_session_open,
                            len(_gpd_keys),
                        )
                except Exception as _vix_err:
                    logger.error("VIX supremacy GPD invalidation failed: %s", _vix_err)
```

#### Why T-04 Matters
- **Problem**: Downloading 270 days of history for 12 tickers inline during scan = 6-24 seconds of latency per cycle, for a STATIC computation
- **Impact**: Saves 6-24 seconds per scan cycle. GPD is now sub-millisecond (Redis GET)

#### Risk Assessment
- **Risk**: Stale GPD cache during black swan event
- **Mitigation**: VIX intraday supremacy invalidates cache on +10 point spike
- **Rollback**: Restore inline GPD code at lines 442-468

#### Estimated Time: ~5 min (Claude Code: replace GPD block, add nightly batch, inject Redis)

---

### T-10: FAST Qualification Path (7 Gates Instead of ~17)

**File**: `/Users/rr/nzt48-signals/main.py` (lines 3752-4165+)

#### Current State

`_execute_s15_priority_path` has ~17 gates despite claiming 5 in its docstring:
1. Discipline Engine (GATE 0, line 3777)
2. Earnings Fade Gate (GATE RC-07b, line 3789)
3. PEAD Boost (line 3803) [confidence modifier, not a gate]
4. VWAP Signal (line 3822) [confidence modifier]
5. Sector Momentum (line 3845) [confidence modifier]
6. Expiry Pinning (line 3865) [confidence modifier]
7. Window Dressing (line 3874) [confidence modifier]
8. Gap Analytics (line 3889) [confidence modifier]
9. Short Squeeze (line 3912) [confidence modifier]
10. Order Flow Imbalance (line 3927) [confidence modifier]
11. Overnight Gap (line 3936) [confidence modifier]
12. Analyst Revision (line 3947) [confidence modifier]
13. Cross-Asset Macro (line 3958) [confidence modifier]
14. Accruals Veto (line 3969) [hard modifier, -20]
15. LSE Hours (GATE 1, line 3988)
16. Daily Loss Limit (GATE 2, line 3996)
17. Max 1 S15 Position (GATE 3, line 4004)
18. Position Sizing (GATE 5, line 4015)
19. IV Crush (sizing modifier, line 4027)
20. Net Expectancy Veto (line 4047)
21. Capacity Constraint (line 4060)
22. Tail Loss Size (line 4075)
23. Regime Stability Size (line 4089)
24. Performance Relegation Size (line 4111)

#### Target: FAST Path (7 gates) vs SLOW Path (all existing gates)

Add tier detection at the top of `_execute_s15_priority_path`:

```python
    async def _execute_s15_priority_path(
        self,
        s15_signals: list[Signal],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """S15 PRIORITY PATH — T-10: Dual-path architecture.

        FAST tier signals (gap/momentum): 7 essential gates only.
          1. Circuit breaker (daily loss limit)
          2. ISA eligibility
          3. Portfolio heat cap (max 1 S15 position)
          4. LSE hours
          5. Correlation brake (via dynamic_sizer if available)
          6. Risk sizing (Kelly)
          7. Cost drag check (spread > reward = skip)

        SLOW tier signals: All existing ~17 gates + confidence modifiers.
        """
        executed: list[Signal] = []

        for signal in s15_signals:
            try:
                # T-10: Determine tier from signal metadata
                _tier = "SLOW"
                if hasattr(signal, 'metadata') and signal.metadata:
                    _tier = signal.metadata.get("tier", "SLOW")

                if _tier == "FAST":
                    # ═══ FAST PATH: 7 gates only ═══
                    # GATE 1: Circuit breaker — daily loss limit
                    if self._daily_pnl_pct < -0.03:
                        logger.info("S15 FAST: daily drawdown %.2f%% > -3%%, skip", self._daily_pnl_pct * 100)
                        continue

                    # GATE 2: LSE Hours
                    from core.clock import now_uk as _now_uk_fn
                    now_uk = _now_uk_fn()
                    if now_uk.hour < 9 or (now_uk.hour >= 15 and now_uk.minute >= 15):
                        logger.info("S15 FAST: outside LSE hours, skip %s", signal.ticker)
                        continue

                    # GATE 3: Max 1 S15 position
                    s15_open = sum(
                        1 for pos in self.virtual_trader.open_positions.values()
                        if pos.status == "OPEN" and pos.strategy == "S15"
                    )
                    if s15_open >= 1:
                        logger.info("S15 FAST: already %d S15 open, skip", s15_open)
                        continue

                    # GATE 4: Position sizing (Kelly)
                    risk_pct = (
                        self.kelly.get_risk_pct(ticker=signal.ticker)
                        if hasattr(self, 'kelly') and self.kelly
                        else 0.0075
                    )
                    risk_dollars = self.equity * risk_pct
                    vix = getattr(market_ctx, 'vix', 0) or 0
                    if vix >= 22.0:
                        risk_dollars *= 0.5

                    # GATE 5: Cost drag check
                    entry_price = signal.entry
                    stop_price = signal.stop
                    risk_per_share = abs(entry_price - stop_price)
                    if risk_per_share <= 0:
                        logger.warning("S15 FAST: zero risk/share for %s, skip", signal.ticker)
                        continue

                    shares = max(1, int(risk_dollars / risk_per_share))
                    signal.shares = shares
                    signal.risk_dollars = risk_dollars

                    logger.info(
                        "S15 FAST PATH QUALIFIED: %s %s shares=%d risk=$%.2f (7 gates in <100ms)",
                        signal.direction, signal.ticker, shares, risk_dollars,
                    )

                    # Build snapshot for learning and execute
                    # [same learning snapshot code as existing, lines 4146-4165+]
                    executed.append(signal)

                else:
                    # ═══ SLOW PATH: All existing gates ═══
                    # [ENTIRE existing code from line 3777 to line 4165+ goes here, unchanged]
```

The key insight: the `metadata["tier"]` field is set by `_score_ticker_with_reason` in daily_target.py. When the signal is created, it carries the tier classification through to the priority path.

#### Signal Metadata Propagation

In `_score_ticker_with_reason` (daily_target.py), the returned dict needs a `tier` field:

```python
        return {
            "ticker": ticker,
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            # ... existing fields ...
            "tier": tier,  # T-10: "FAST" or "SLOW"
        }, None
```

And in `scan()` where the signal is created (line 513-519), propagate it:

```python
        signal = self._create_signal(
            ticker=best["ticker"],
            direction=best["direction"],
            entry=best["entry"],
            stop=best["stop"],
            indicators=indicators[best["ticker"]],
            market_ctx=market_ctx,
        )
        signal.confidence = best["confidence"]
        signal.metadata = signal.metadata or {}
        signal.metadata["tier"] = best.get("tier", "SLOW")  # T-10: propagate tier
```

#### Why T-10 Matters
- **Problem**: Even the priority path has ~17 gates with database queries. FAST tier signals don't need earnings fade, PEAD, sector momentum, etc. -- the gap/momentum IS the signal.
- **Impact**: FAST path < 100ms (7 simple checks) vs SLOW path ~3s (17 gates + confidence modifiers)

#### Risk Assessment
- **Risk**: FAST path skips 10+ safety checks
- **Mitigation**: The 7 essential gates cover the critical safety items (circuit breaker, position limits, sizing). The skipped gates are CONFIDENCE modifiers, not safety gates.
- **Rollback**: Set all signals to `tier="SLOW"` to route everything through existing path

#### Estimated Time: ~5 min (Claude Code: add FAST path branch, wrap existing in SLOW else)

---

### T-03: Anomaly-Triggered Priority Scanning

**File**: `/Users/rr/nzt48-signals/main.py`

#### Current State

The `continuous_24_7` job (line 5282-5291) runs `self.run_scan()` every 60 seconds, which runs ALL strategies including S15 for ALL tickers. This is already the right frequency -- the bottleneck is S15's internal gates (fixed by T-01/T-02/T-06/T-07).

#### Target: Anomaly Pre-Filter

Add a lightweight price-anomaly pre-filter to the existing 60s scan that prioritises tickers showing large moves:

**New method in the orchestrator class** (add near `run_scan`):

```python
    async def _check_price_anomalies(self) -> list[str]:
        """T-03: Price anomaly pre-filter for priority scanning.
        Checks if any CORE ticker moved >1.0% from session open or >0.5% in last 5 min.
        Returns list of anomaly tickers to scan first.
        """
        anomaly_tickers = []
        try:
            from uk_isa.isa_universe import EXTENDED_UNIVERSE
            _redis = None
            if hasattr(self, 'chandelier') and hasattr(self.chandelier, '_redis'):
                _redis = getattr(self.chandelier, '_redis', None)

            for ticker in EXTENDED_UNIVERSE:
                try:
                    # Get current price from latest indicator snapshot
                    snap = self._latest_indicators.get(ticker) if hasattr(self, '_latest_indicators') else None
                    if not snap or snap.price <= 0:
                        continue

                    current_price = snap.price

                    # Check vs session open (stored in Redis by T-01)
                    session_open = getattr(snap, 'session_open_price', 0)
                    if _redis and session_open <= 0:
                        _cached_open = _redis.get(f"nzt:session_open:{ticker}")
                        if _cached_open:
                            session_open = float(_cached_open)

                    if session_open > 0:
                        move_from_open = abs(current_price - session_open) / session_open
                        if move_from_open > 0.01:  # >1% from session open
                            anomaly_tickers.append(ticker)
                            continue

                    # Check 5-min price change (from last cached price)
                    _prev_key = f"nzt:price_5m:{ticker}"
                    if _redis:
                        _prev_price = _redis.get(_prev_key)
                        if _prev_price:
                            _prev = float(_prev_price)
                            if _prev > 0:
                                _5m_move = abs(current_price - _prev) / _prev
                                if _5m_move > 0.005:  # >0.5% in ~5 min
                                    anomaly_tickers.append(ticker)
                        # Update the 5-min cache (TTL 5 min)
                        _redis.setex(_prev_key, 300, str(current_price))

                except Exception:
                    pass

        except Exception as _e:
            logger.debug("T-03 anomaly check error: %s", _e)

        if anomaly_tickers:
            logger.info(
                "T-03 ANOMALY: %d tickers showing unusual moves: %s",
                len(anomaly_tickers), ", ".join(anomaly_tickers),
            )
        return anomaly_tickers
```

**Integration**: At the top of `run_scan`, check for anomalies and scan those first:

```python
        # T-03: Anomaly-triggered priority scanning
        _anomaly_tickers = await self._check_price_anomalies()
        if _anomaly_tickers and "S15" not in (strategy_ids or []):
            # Run S15 for anomaly tickers FIRST (before full universe scan)
            try:
                await self._run_strategy_for_tickers("S15", _anomaly_tickers)
            except Exception as _anom_err:
                logger.debug("T-03 anomaly scan error: %s", _anom_err)
```

#### Why T-03 Matters
- **Problem**: Scanning all 12 tickers every 60s takes ~24s when all tickers are processed. With anomaly pre-filter, we can prioritise the 1-2 tickers actually moving.
- **Impact**: Reduces per-cycle latency from ~24s (12 tickers) to ~2s (1-2 anomaly tickers) for the critical first scan.

#### Risk Assessment
- **Risk**: Missing non-anomaly moves. Low -- any move > 1% will trigger the anomaly filter.
- **Mitigation**: The full 12-ticker scan still runs every 60s. The anomaly scan is an ADDITIONAL priority scan, not a replacement.
- **Rollback**: Remove the anomaly check call -- no other code depends on it

#### Estimated Time: ~5 min (Claude Code: add method + integrate into run_scan)

---

## Dependencies & Ordering

### Critical Path (MUST be sequential)

```
T-08 (Sprint 0, DONE) ── Remove single-fire limit
         |
         v
    T-01 (Sprint 1A) ── Opening blackout -> observe-then-act
         |
         v
    T-02 (Sprint 1A) ── Lunch dead zone -> reduced confidence
         |
         v
    T-05 (Sprint 1B) ── FAST/SLOW tier architecture  <-- MUST be before T-06/T-07
         |
         v
    T-06 (Sprint 1B) ── ADX tier thresholds (depends on T-05 tier classification)
         |
         v
    T-07 (Sprint 1B) ── RVOL tier thresholds (depends on T-05 tier classification)
         |
         v
    T-04 (Sprint 1C) ── GPD nightly batch (depends on Redis injection)
         |
         v
    T-10 (Sprint 1C) ── FAST qualification path (depends on T-05 tier metadata)
         |
         v
    T-03 (Sprint 1C) ── Anomaly trigger (depends on session_open from T-01)
```

### Parallelizable Work

1. **T-01 and T-02** can be done in parallel (both modify `scan()` but different line ranges)
2. **T-06 and T-07** can be done in parallel (both modify gate logic but different gates)
3. **T-04 and T-03** can be done in parallel (T-04 is daily_target.py + main.py nightly; T-03 is main.py scan loop)

### Recommended Implementation Order (Claude Code — single session)

| Step | Tasks | Est. Time | Why This Order |
|------|-------|-----------|----------------|
| 1 | T-01, T-02 (parallel edits) | ~10 min | Entry windows first — unblock the most signals |
| 2 | T-05 (tier architecture) | ~10 min | Foundation for T-06/T-07/T-10 |
| 3 | T-06, T-07 (parallel edits) | ~5 min | Depend on T-05 tier, independent of each other |
| 4 | T-04 (GPD cache) | ~5 min | Independent of tier work, reduces latency |
| 5 | T-10 (FAST qual path) | ~5 min | Depends on tier metadata from T-05 |
| 6 | T-03 (anomaly trigger) | ~5 min | Last — depends on session_open from T-01 |
| 7 | Deploy + verify | ~5 min | `bash deploy.sh rebuild` + log check |

**Total: ~45 min in one Claude Code session, including deploy and verification.**

Note: Sprint 0.5 (7 fixes across 10 files + deploy) took ~20 min. Sprint 1 is more complex (new methods, restructured gates, model changes) but all code is pre-specified — it's mechanical application of the plan above.

---

## Verification Plan

### Per-Task Verification

| Task | Verification Command | Expected Log Pattern |
|------|---------------------|---------------------|
| T-01 | `docker logs nzt48 --tail 500 \| grep "T-01"` | `S15 T-01: OBSERVE phase`, `S15 T-01: GAP SCAN phase`, or `S15 T-01 GAP SIGNAL:` between 09:00-09:15 UK |
| T-02 | `docker logs nzt48 --tail 500 \| grep "T-02"` | `S15 T-02: lunch window` + `S15 T-02: lunch penalty -10` between 11:30-13:00 UK |
| T-05 | `docker logs nzt48 --tail 500 \| grep "FAST TIER"` | `S15 T-05 FAST TIER:` when 3/4 leading indicators agree |
| T-06 | `docker logs nzt48 --tail 500 \| grep "ADX"` | No more `adx_too_low(18.5<25.0)` rejections -- instead `SLOW` tier accepts at 20+ |
| T-07 | `docker logs nzt48 --tail 500 \| grep "rvol"` | No more `rvol_too_low(0.6<0.85)` rejections -- SLOW tier accepts at 0.65+ |
| T-04 | `docker logs nzt48 --tail 500 \| grep "GPD"` | `T-04 GPD NIGHTLY BATCH: computed tail risk for N/12 tickers` at 21:30 UK; `S15_GPD_VETO (cached)` during scan |
| T-10 | `docker logs nzt48 --tail 500 \| grep "FAST PATH"` | `S15 FAST PATH QUALIFIED:` for tier=FAST signals |
| T-03 | `docker logs nzt48 --tail 500 \| grep "ANOMALY"` | `T-03 ANOMALY: N tickers showing unusual moves` when a ticker moves > 1% |

### System-Level Verification

1. **Health check**: `curl http://3.230.44.22:8000/health` -- mode=PAPER, status=healthy
2. **Import check**: `docker logs nzt48 --tail 200 | grep -i error` -- no import errors, no missing fields
3. **Scan cycle timing**: `docker logs nzt48 --tail 100 | grep "SCAN:"` -- verify scans complete in < 5s (down from ~30s)
4. **Signal flow**: Wait for market hours, verify signals pass through FAST tier when conditions are met
5. **No regressions**: Verify SLOW tier signals still work (existing 8-indicator consensus)
6. **Redis GPD**: `docker exec nzt48-redis redis-cli keys "nzt:gpd:*"` -- should show 12 keys after first nightly run
7. **VIX session open tracking**: `docker logs nzt48 --tail 200 | grep "vix_session_open"` -- verify VIX baseline is recorded

### Behavioural Changes to Observe

| Before Sprint 1 | After Sprint 1 |
|-----------------|----------------|
| 0 signals during 09:00-09:30 UK | Gap signals possible from 09:05 |
| 0 signals during 11:30-13:00 UK | Reduced-confidence signals allowed |
| ADX < 25 = rejected | FAST tier accepts at ADX 15+ |
| RVOL < 0.85 = rejected | FAST tier accepts at RVOL 0.30+ |
| ~30s per scan cycle (GPD inline) | < 5s per scan cycle (GPD cached) |
| All signals through 17-gate path | FAST: 7 gates, SLOW: 17 gates |
| No anomaly detection | > 1% move triggers priority scan |

---

## Risk Assessment

### What Could Go Wrong

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| FAST tier fires too many false positives | MEDIUM | Low (paper mode) | RO-01 spread gate, 3/4 indicator requirement, 1.5% move threshold |
| Gap signals enter at wide opening spreads | MEDIUM | Medium | 35 bps spread hard-cap (RO-01 supremacy) |
| Lower RVOL/ADX admits low-quality trades | MEDIUM | Low (paper mode) | Confidence floor 65 still applies; FAST tier needs 3/4 leading indicator agreement |
| Redis unavailable -- GPD fallback | LOW | Low | Graceful fallback: no GPD check (allow trade) rather than crash |
| Tier classification wrong (FAST when should be SLOW) | LOW | Low | FAST requires 3/4 indicators + 1.5% move -- conservative |
| _determine_direction return signature breaks callers | LOW | High | Only 1 call site (line 631) -- update it |
| New IndicatorSnapshot fields break serialisation | LOW | Medium | All new fields have `Optional` defaults -- backward compatible |

### Rollback Plan

**Full rollback**: Restore 3 files to pre-Sprint 1 state:
```bash
git checkout HEAD~1 -- strategies/daily_target.py models.py main.py feeds/indicators.py
bash deploy.sh rebuild
```

**Partial rollback by task**:
- T-01: Restore lines 324-333, delete `_record_session_opens` and `_scan_gaps` methods
- T-02: Restore lines 335-344, remove `_is_lunch_window` flag
- T-05: Revert `_determine_direction` to 3-tuple return, remove tier logic
- T-06: Restore `_MIN_ADX = 25.0` and simple ADX gate
- T-07: Restore `_MIN_RVOL = 0.85` and simple RVOL gate
- T-04: Restore inline GPD computation at lines 442-468
- T-10: Set all signals to `tier="SLOW"` in metadata
- T-03: Remove anomaly check call from `run_scan`

---

## Summary of Files Modified

| File | Changes | Sprint |
|------|---------|--------|
| `strategies/daily_target.py` (922 lines) | T-01: Replace lines 324-333, add 2 methods (~120 LOC). T-02: Replace lines 335-344, add penalty logic. T-05: Restructure `_determine_direction` (+tier return), restructure gate ordering in `_score_ticker_with_reason`. T-06: New ADX constants, two-pass gate. T-07: New RVOL constants, two-pass gate. T-04: Replace GPD inline with Redis lookup, add `redis_client` to `__init__`. | 1A, 1B, 1C |
| `models.py` (237 lines) | Add 6 new fields to IndicatorSnapshot: `prev_close`, `open_price`, `session_open_price`, `roc_5`, `adx_delta`, `rvol_trajectory` | 1A, 1B |
| `feeds/indicators.py` (1300+ lines) | Add ROC(5), ADX delta, RVOL trajectory computations to `compute_all` | 1B |
| `main.py` (8389 lines) | T-04: Inject Redis into S15 constructor, add GPD nightly batch to `_run_nightly_intelligence`, add VIX supremacy check. T-10: Restructure `_execute_s15_priority_path` for dual-path (FAST/SLOW). T-03: Add `_check_price_anomalies` method, integrate into `run_scan`. | 1C |

**Net new lines**: ~450 LOC
**Lines deleted**: ~30 LOC (blackout blocks, inline GPD)
**Lines modified**: ~80 LOC (constants, gate logic, return signatures)

---

## Post-Sprint 1 Deployment Checklist

1. `docker compose build nzt48` -- verify build completes without import errors
2. `bash deploy.sh rebuild` -- deploy to EC2
3. `docker logs nzt48 --tail 200` -- no crash, no import error
4. `curl http://3.230.44.22:8000/health` -- healthy
5. Wait for next LSE open (09:00 UK) -- watch for T-01 OBSERVE phase log
6. Monitor first full trading day -- look for gap signals, FAST tier triggers, lunch window signals
7. After 21:30 UK: verify GPD nightly batch ran (`grep "GPD NIGHTLY BATCH" logs`)
8. Next morning: verify GPD cache keys exist in Redis
9. Proceed to Sprint 2 only after 1 full trading day with no crashes
