# Gemini Adversarial Audit — Sprints 0, 1, 2 Combined

Copy everything below the line into Gemini.

---

You are a 4-persona adversarial review board auditing code changes to NZT-48 — a fully automated leveraged-ETP trading engine running 24/7 on EC2 in paper mode. The system trades UK ISA 3x/5x leveraged ETPs on the LSE.

## Your Personas

1. **Chief Quant Officer (CQ)** — Signal validity, statistical foundations, false positive rates, indicator interactions, regime sensitivity
2. **Lead Systems Architect (SA)** — Thread safety, async/sync boundaries, Redis access patterns, container lifecycle, deployment correctness
3. **Chief Risk Officer (CRO)** — Loss limits, position sizing, circuit breakers, tail risk, fail-closed vs fail-open, catastrophic scenarios
4. **Academic Reviewer (AR)** — Citation accuracy, methodology validity, parameter choices, look-ahead bias, overfitting risk

## System Context

- **Architecture**: Python 3.11, APScheduler (60s cron), Docker Compose (engine + IB Gateway + Redis)
- **Strategy**: S15 "2% Daily Target" — momentum/gap strategy on 12 LSE leveraged ETPs
- **Starting equity**: £10,000 paper ISA
- **Problem**: S15 had 0% win rate across 52 paper trades. Root cause: execution timing (enters 15-60 min late, move already 80-95% complete, mean reversion stops out)
- **Solution**: 3 sprints of code changes (Sprint 0: quick wins, Sprint 1: timing rescue, Sprint 2: silent killers)

## What To Audit

Below are ALL changes across Sprints 0, 1, and 2. Audit every change for correctness, safety, and interactions between changes.

---

### SPRINT 0: Quick Wins (7 items) — DEPLOYED

**S0-1: VIX Fail-Closed** (`core/cross_asset_macro.py`)
- VIX fetch failure now defaults to 35.0 (RISK_OFF) instead of 0.0 (RISK_ON)
- Rationale: fail-closed is safer — missing VIX data should block risky trades, not enable them

**S0-2: Confidence Floor Alignment** (`strategies/daily_target.py` line 74)
- Changed `_MIN_CONFIDENCE = 75.0` → `65.0` to align with `risk_sizer.py` floor (was rejecting signals that risk_sizer would have accepted)

**S0-3: Single-Fire Removal** (`strategies/daily_target.py` line 304)
- Changed from boolean `_daily_signal_fired` (1 signal/day max) to count-based `_daily_signal_count` dict with `_MAX_SIGNALS_PER_DAY = 3`
- Rationale: single-fire killed recovery trades after a morning loss

**S0-4: +1.5% Session Halt Removal** (`strategies/daily_target.py`)
- Removed early exit when daily P&L >= +1.5% — this was leaving 30%+ of the daily target on the table

**S0-5: Timezone Alignment** (`strategies/daily_target.py`)
- All time comparisons now use `_UK_TZ` (Europe/London) consistently — was mixing UTC and UK time causing signals to fire outside intended windows

**S0-6: List Mutation Fix** (`strategies/daily_target.py`)
- Fixed in-place list mutation during iteration that could skip tickers or cause index errors

**S0-7: Data Freshness Gate** (`strategies/daily_target.py` lines 442-467)
- NEW: Rejects scan if ALL indicator data is stale (>120s old)
- Per-ticker stale data check skips individual tickers with stale snapshots
- Rationale: stale indicators on 3x/5x leveraged ETPs = guaranteed wrong entry

---

### SPRINT 1: Timing Rescue (10 changes across 4 files) — DEPLOYED

#### T-01: Opening Gap Protocol (`daily_target.py` lines 347-400)

REPLACES the 30-min hard blackout (which vetoed 100% of opening gap signals) with 3-phase protocol:
- **Phase 1 (09:00-09:05 UK)**: OBSERVE — record session open prices, `return []`
- **Phase 2 (09:05-09:15 UK)**: GAP SCAN — fire on leverage-scaled gap thresholds using executable price (Ask for LONG, Bid for SHORT — NOT mid-price)
  - 3x ETPs: gap > 2.5% (= ~0.83% underlying move)
  - 5x ETPs: gap > 4.0% (= ~0.80% underlying move)
  - Subject to RO-01 spread gate (35 bps)
- **Phase 3 (09:15+)**: Normal scanning

New methods: `_record_session_opens()`, `_scan_gaps()`
Init: `self._session_opens = {}`, injected via constructor

#### T-02: Lunch Window Penalty (`daily_target.py` lines 311, 655)

REPLACES the 90-min lunch dead zone (hard `return []`) with:
- `self._is_lunch_window = (now_uk.hour == 11 and now_uk.minute >= 30) or now_uk.hour == 12`
- Computed as EXPRESSION each scan call (not one-directional flag — was a bug found in adversarial review)
- RVOL floor during lunch: `_MIN_RVOL_LUNCH = 0.50` (reduced from 0.60)
- FAST tier signals BLOCKED entirely during lunch (not just penalized) — gap signals from 09:05 are stale by 11:30

#### T-03: Anomaly-Triggered Priority Scanning (`main.py` lines 1350-1430, 1536-1540)

New method `_check_price_anomalies()`:
- For each CORE ticker, checks if price moved >1% from session open or >0.5% in last 5 min
- Uses Redis `nzt:price_5m:{ticker}` with 300s TTL for 5-min price snapshots
- 30s debounce via `nzt:last_scan:{ticker}` — prevents re-scanning within 30s
- Returns list of anomaly tickers

Integration: anomaly tickers PREPENDED to existing ticker list (no recursive run_scan call — that would bypass safety gates):
```python
tickers = anomaly_tickers + [t for t in tickers if t not in anomaly_set]
```
APScheduler `max_instances=1` + `coalesce=True` prevents concurrent execution.

#### T-04: GPD Cache + VIX Supremacy (`daily_target.py` lines 302-313, 405-440; `main.py` lines 1327-1337, 7886-7945)

**GPD to Redis cache**: Replaced inline yfinance download (6-24s latency per ticker) with Redis GET:
- S15 constructor accepts `redis_client=None` — synchronous `redis.Redis` client (NOT async)
- `main.py` line 1331: creates sync Redis client, injects into S15 via `_s15_kwargs["redis_client"]`
- GPD check: `GET nzt:gpd:{ticker}` → parse JSON `{veto: bool, reason: str}`
- If key missing AND `_emergency_tail_risk_veto` is True → VETO (fail-closed)
- If key missing AND no emergency → allow trade (conservative default)

**Nightly batch** (`main.py` lines 7886-7945):
- Loops `EXTENDED_UNIVERSE` tickers
- Downloads 270d daily via yfinance, computes GPD via `TailRiskMonitor`
- `SETEX nzt:gpd:{ticker} 86400 {json}` — 24h TTL
- After successful batch: clears `_emergency_tail_risk_veto` on all strategies

**VIX Supremacy** (`daily_target.py` lines 405-440):
- Session open VIX captured on first non-fallback scan (skips VIX=35.0 default)
- If VIX delta > +10 from session open: sets `_emergency_tail_risk_veto = True` AND deletes all `nzt:gpd:*` keys via SCAN+DEL pattern (not bare DEL with glob)
- Fail-CLOSED: emergency veto blocks all new LONG entries until nightly batch recomputes
- Veto cleared at: (a) daily reset, (b) after successful nightly GPD batch

#### T-05: FAST/SLOW Tier Architecture (`daily_target.py` lines 925-1073, 726, 893-922)

Modified `_determine_direction` return from `tuple[str, float, float]` to `tuple[str, float, float, str]`.

**FAST tier check** — 4 leading indicators:
1. VWAP (price vs vwap)
2. MACD histogram (sign)
3. RSI (>55 long, <45 short)
4. ROC(30) with explicit None guard: `roc_30 is not None and roc_30 > 1.5`

FAST qualifies when: 3/4 agree AND price moved ≥ leverage-scaled threshold (2.5% for 3x, 4.0% for 5x).
Tier classification: `tier = "FAST" if is_fast_qualified and fast_direction == direction else "SLOW"`

FAST tier gets bonus confidence from agreeing SLOW indicators (+0 to +3.5).
Tier propagated through signal metadata: `signal.metadata["tier"] = best.get("tier", "SLOW")`

#### T-06: ADX Tiered Thresholds (`daily_target.py` lines 80-84, 664-672, 729-734)

New constants:
```python
_MIN_ADX_FAST = 15.0       # Catch trend birth
_MIN_ADX_SLOW = 20.0       # Moderate confirmation
_MIN_ADX_RANGE_BOUND = 25.0  # Strict in chop
_ADX_ACCEL_THRESHOLD = 2.0   # ADX rising > 2 pts/bar
```

Two-pass gate:
- Pass 1 (pre-filter, line 666): reject below FAST floor (15)
- Pass 2 (after tier, line 730-734): SLOW requires ≥20 (or ≥25 in RANGE_BOUND)
- ADX acceleration bonus: +5.0 effective ADX if `adx_delta > 2.0`, but ONLY after pre-filter (cannot rescue sub-floor ADX)

#### T-07: RVOL Tiered Thresholds (`daily_target.py` lines 66-72, 653-662, 736-739)

New constants:
```python
_MIN_RVOL_FAST = 0.60      # Minimum viable liquidity
_MIN_RVOL_SLOW = 0.65      # Institutional participation
_MIN_RVOL_LUNCH = 0.50     # Reduced during lunch
_MIN_RVOL_RANGE_BOUND = 1.2  # Strict in chop
_RVOL_RISING_THRESHOLD = 2.0  # Trajectory > 2x = confirming
```

Two-pass gate:
- Pass 1 (pre-filter, line 656): reject below FAST floor (0.60), or lunch floor (0.50)
- Pass 2 (after tier, line 736-739): SLOW requires ≥0.65 (or ≥1.2 in RANGE_BOUND)
- RVOL trajectory override: if RVOL ≥ 0.60 AND trajectory > 2x, relax SLOW to FAST threshold. Never bypasses FAST floor

#### T-08: New Indicator Fields (`models.py` lines 238-241, `feeds/indicators.py` lines 261-310)

3 new fields on `IndicatorSnapshot`:
```python
roc_30: Optional[float] = None      # 30-bar ROC (%) — 30 min on 1-min bars
adx_delta: Optional[float] = None   # ADX change per bar
rvol_trajectory: Optional[float] = None  # Current RVOL / mean of last 3 bars' RVOL
```

Computations in `compute_all()`:
- **ROC(30)**: `(close[-1] - close[-31]) / close[-31] * 100` — needs ≥31 bars. Threshold 1.5% (raised from initial ROC(5) which was pure noise)
- **ADX delta**: Reuses `ta.adx()` result, `adx_series.iloc[-1] - adx_series.iloc[-2]`
- **RVOL trajectory**: `current_rvol / mean(last 3 bars' RVOL)` — each bar's RVOL = bar_vol / rolling_20_mean

All wrapped in try/except (non-fatal, defaults to None). All usage sites use explicit None guards.

#### T-10: FAST Qualification Path (`main.py` lines 3867-3932)

In `_execute_s15_priority_path`, signal tier extracted from metadata:
- **FAST tier** → 8 essential gates only:
  1. Discipline Engine (absolute authority)
  2. Circuit breaker (daily loss < -3%)
  3. LSE hours (already checked by S15.scan())
  4. Max 1 S15 position open
  5. FAST lunch block (11:30-13:00 UK → `continue`, not penalty)
  6. VIX filter (half-size above 22, block 5x above 22)
  7. Position sizing (quarter-Kelly, capped 0.75% risk)
  8. Share calculation
  - Skips 9 confidence modifiers (PEAD, VWAP, sector momentum, expiry pinning, etc.)
  - All confidence modifier blocks gated by `if _signal_tier != "FAST"`

- **SLOW tier** → all existing gates + confidence modifiers unchanged

#### Daily Reset Enhancement (`daily_target.py` lines 336-345)

At start of each trading day:
- Clears `_emergency_tail_risk_veto = False`
- Resets `_session_open_vix = None`
- Clears `_session_opens = {}` (stale from yesterday)

---

### SPRINT 2: Silent Killers (2 bugs across 5 files) — DEPLOYED

#### SK-01: Equity Denominator Bug

**Problem**: `_starting_equity` in circuit_breakers.py was frozen at init value (10000.0). As equity grows/shrinks over days, ALL daily P&L % calculations were wrong. Circuit breaker drawdown check (`loss_pct = abs(min(daily_pnl, 0)) / self._starting_equity` at line 387) divides by stale equity.

**Fix 1** — `qualification/circuit_breakers.py` (line 298):
```python
def reset_daily(self, current_equity: float | None = None) -> None:
    self._consecutive_losses = 0
    self._last_loss_time = None
    self._cooldown_until = None
    self._vix_pause_until = None
    self._halted_for_session = False
    self._halt_reason = ""
    self._daily_results.clear()
    # SK-01: Update starting equity for daily P&L % calculations
    if current_equity is not None and current_equity > 0:
        self._starting_equity = current_equity
        logger.info("Circuit breaker daily reset — equity updated to %.2f", current_equity)
    else:
        logger.info("Circuit breaker daily state reset.")
```
Optional default preserves backward compatibility for tests.

**Fix 2** — `delivery/sheets_logger.py` (line 57):
```python
def __init__(self, spreadsheet_name="NZT-48 Trade Log", starting_equity: float = 10_000.0) -> None:
    self._starting_equity = starting_equity
    self._current_equity = starting_equity
```
Replaced hardcoded `10000.0` with parameter.

**Fix 3** — `main.py` (line 6757):
```python
self.circuit_breakers.reset_daily(current_equity=self.equity)
```

**Fix 4** — `main.py` (line 678):
```python
self.sheets = SheetsLogger(starting_equity=self.equity)
```

**NOT fixed** — `dynamic_sizer.py`: Does NOT need fixing. Receives live equity as parameter on each `calculate_position_size(equity, ...)` call. Its `_starting_equity` is only used in init log message, not in calculations.

#### SK-02: Zombie Halt Bug

**Problem**: Consecutive loss SQL queries had no time filter. Ancient losses from weeks/months ago triggered permanent halts even when recent trades were winning.

**Fix 1** — `main.py` (lines 1176-1187):
```python
rows = conn.execute(
    """SELECT net_pnl FROM virtual_trades
       WHERE exit_time >= datetime('now', '-12 hours')
       ORDER BY exit_time DESC LIMIT 10"""
).fetchall()
if not rows:
    rows = conn.execute(
        """SELECT pnl_dollars as net_pnl FROM trades
           WHERE time_exited >= datetime('now', '-12 hours')
           ORDER BY time_exited DESC LIMIT 10"""
    ).fetchall()
```
Also fixed column name: `time_entered` → `time_exited` (we care about when the loss was realised, not when the trade was entered). Fixed `ORDER BY time_entered` → `ORDER BY time_exited`.

**Fix 2** — `delivery/database.py` (lines 1008-1036):
```python
def get_consecutive_losses(conn, bot_instance):
    """SK-02: Only considers trades from the last 12 hours."""
    rows = conn.execute(
        """SELECT net_pnl as pnl FROM virtual_trades
           WHERE bot_instance = ?
             AND exit_time >= datetime('now', '-12 hours')
           ORDER BY exit_time DESC LIMIT 10""",
        (bot_instance,)
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """SELECT pnl_dollars as pnl FROM trades
               WHERE bot_instance = ?
                 AND time_exited >= datetime('now', '-12 hours')
               ORDER BY time_exited DESC LIMIT 10""",
            (bot_instance,)
        ).fetchall()
```
Same changes: 12h time filter + column name fix.

**Fix 3** — `qualification/go_nogo.py` (lines 157-161):
```python
all_results = conn.execute(
    """SELECT r_multiple FROM virtual_trades
       WHERE exit_time >= datetime('now', '-30 days')
       ORDER BY exit_time ASC"""
).fetchall()
```
Uses **30-day** window (not 12h) because Go/No-Go evaluates system fitness over a rolling month. A 12h window would only see 1-2 trades, making the gate useless. The point is to prevent ANCIENT losses from permanently blocking the system, while still checking recent streak health.

---

## Your Task

Produce a COMPREHENSIVE adversarial review across all 3 sprints. For each finding:

**Verdict**: ✅ CORRECT | ⚠️ CONCERN | ❌ BUG | 🔧 REVERT

### Section 1: Per-Persona Findings

Each persona MUST produce **at least 10 findings** covering:
- Individual change correctness
- **Cross-sprint interactions** (e.g., does Sprint 1's RVOL change interact badly with Sprint 0's confidence floor change?)
- Edge cases under extreme market conditions
- Fail modes (what happens when Redis is down, VIX data missing, yfinance returns empty, etc.)

For each finding, provide:
- Finding ID (e.g., CQ-1, SA-3, CRO-7, AR-2)
- Severity: CRITICAL / HIGH / MEDIUM / LOW
- The specific change(s) involved (Sprint + Task ID)
- What's wrong or what could go wrong
- Recommended fix (if applicable)
- Verdict: ✅ / ⚠️ / ❌ / 🔧

### Section 2: Cross-Sprint Interaction Matrix

Identify at least 5 specific interactions between changes across different sprints that could produce emergent behaviour (good or bad).

### Section 3: Failure Cascade Analysis

For each of these failure scenarios, trace the cascade through ALL sprint changes:
1. Redis goes down during market hours
2. VIX data unavailable for entire trading day
3. yfinance returns empty data for all tickers
4. IBKR Gateway disconnects mid-trade
5. Container restart at 09:10 UK (during GAP SCAN phase)

### Section 4: Statistical Validity

Evaluate whether the combined changes create any of these anti-patterns:
- Parameter overfitting (too many thresholds tuned to historical data)
- Look-ahead bias in any computation
- Survivorship bias in ticker universe
- Multiple comparison problem (many gates = high false negative rate)
- Multicollinearity between indicators

### Section 5: Summary Scorecard

Rate each sprint on a 1-10 scale for:
- Correctness
- Safety (fail-closed?)
- Maintainability
- Expected impact on win rate

### Section 6: Top 5 REVERT Candidates

List the 5 changes most likely to cause harm in production, ranked by risk. For each, explain what would happen if the change went wrong and whether the rollback path is clean.

## IMPORTANT RULES

1. **Be adversarial, not confirmatory.** Your job is to find problems, not praise the work.
2. **Be specific.** Reference exact line numbers, variable names, and code paths.
3. **Don't invent phantom bugs.** If a concern requires assumptions about code you haven't seen, state the assumption explicitly.
4. **Cross-validate between personas.** If CQ finds an indicator concern, CRO should evaluate the risk impact.
5. **Minimum 40 total findings across all 4 personas (10 each minimum).**
6. **Every finding must have a verdict.**
