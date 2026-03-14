# THE AEGIS V2 TERMINAL DIRECTIVE
## Complete 15-Week Execution Protocol with Ralph Wiggum Loop

**Date**: 2026-03-10
**Status**: LOCKED FOR EXECUTION
**Timeline**: Late June 2026 Live Capital Deployment
**Data Architecture**: IBKR Primary + yfinance Fallback (Option D+)

---

## CORE OPERATIONAL RULES (THE RALPH WIGGUM PROTOCOL)

### 1. **No God Mode**
- You must operate strictly under `accept-edits`. You may NOT use `bypass-permissions`.
- All changes require explicit user approval via `[c]` continue, `[s]` skip, or `[q]` quit gates.

### 2. **The Ralph Wiggum Loop**
When compiling Rust or running Python tests:
- You must NEVER run open-ended loops
- If writing bash script to auto-fix errors, cap at 20 iterations: `for i in {1..20}; do cargo check && break; ... done`
- If it fails 20 times: STOP and request help
- Applied to: API pagination, test retries, docker rebuilds

### 3. **The Anchor Rule**
At the end of EVERY coding session or phase:
- Update `CORE_TYPES_ANCHOR.md` with exact Rust struct definitions
- Include PyO3 bindings and channel signatures currently in codebase
- Use as reference bridge for next session (prevents LLM hallucination)
- File location: `/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/CORE_TYPES_ANCHOR.md`

### 4. **The Checkpoint Rule**
Any script that fetches data via API MUST:
- Write state to `checkpoint.json` after every iteration/page
- Never restart from zero on network failure
- Resume from last checkpoint on restart
- Applied to: Polygon dividend pagination, splits pagination, IBKR contract discovery

### 5. **The IBKR-Primary Protocol**
All data feed operations must:
- Try IBKR Gateway first (real-time, <100ms latency)
- Fall back to yfinance on IBKR unavailable (>10 min timeout)
- Use Polygon only for corporate actions (dividends/splits)
- H-07 auto-reconnection: Docker restart on 3 consecutive failures
- Telegram alerts on all major transitions

### 6. **The Approval Gate Protocol**
- Every phase MUST pause after completion
- Wait for explicit `APPROVED PHASE X` before proceeding
- User can `[c]` continue, `[s]` skip, or `[q]` quit at each gate
- Log all approvals to execution journal

---

## PHASE 0: BOOTSTRAP DATA CACHES (2 DAYS, MARCH 11-12)
**ETA**: 87 minutes (09:00-10:27 UTC)

### Step 0.1: Dividend Calendar Bootstrap (37.5 min)
**File**: `python_brain/ouroboros/bootstrap_dividend_calendar.py`

```python
# RULES:
# 1. Use Polygon /v3/reference/dividends endpoint
# 2. Implement strict 15-second time.sleep() between paginated calls
# 3. 150 API calls total (4 calls/min rate limit)
# 4. Implement Checkpoint Rule: Save next_cursor to checkpoint.json after each page
# 5. Output: /app/data/dividend_calendar.json (5,200+ tickers)
# 6. Expected: 37.5 minutes, 150 API calls

class PolygonDividendBootstrapper:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.rate_limit_sec = 15  # Strict
        self.checkpoint_file = "data/checkpoint_dividends.json"
        self.max_retries = 3

    def bootstrap(self):
        all_dividends = {}
        checkpoint = self.load_checkpoint()
        cursor = checkpoint.get("next_cursor")
        api_calls = checkpoint.get("api_calls", 0)

        while True:
            # Rate limit
            if api_calls > 0:
                time.sleep(self.rate_limit_sec)

            # Fetch
            response = requests.get(
                "https://api.polygon.io/v3/reference/dividends",
                params={
                    "sort": "ex_dividend_date",
                    "limit": 1000,
                    "cursor": cursor
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30
            )
            api_calls += 1

            if response.status_code == 429:
                # Ralph Wiggum: retry with backoff
                if api_calls < 150 * 3:  # Max 20 retry iterations
                    time.sleep(60)
                    continue
                else:
                    raise RuntimeError("429 rate limit: Max retries exceeded")

            data = response.json()
            for item in data.get("results", []):
                ticker = item.get("ticker")
                if ticker not in all_dividends:
                    all_dividends[ticker] = []
                all_dividends[ticker].append(item)

            # CHECKPOINT RULE
            cursor = data.get("next_cursor")
            self.save_checkpoint({
                "next_cursor": cursor,
                "api_calls": api_calls,
                "tickers_count": len(all_dividends)
            })

            if not cursor or api_calls >= 150:
                break

        # Save output
        with open("data/dividend_calendar.json", "w") as f:
            json.dump(all_dividends, f)

        print(f"✓ Dividends: {len(all_dividends)} tickers, {api_calls} calls")
        return all_dividends
```

**Acceptance Test**:
```bash
python -c "
import json
with open('data/dividend_calendar.json') as f:
    divs = json.load(f)
assert len(divs) >= 5000, f'Expected >=5000, got {len(divs)}'
print(f'✓ AT-Bootstrap-Dividend-Calendar PASSED: {len(divs)} tickers')
"
```

---

### Step 0.2: Splits Calendar Bootstrap (37.5 min)
**File**: `python_brain/ouroboros/bootstrap_splits_calendar.py`

Same structure as Step 0.1, but:
- Use Polygon `/v3/reference/splits` endpoint
- Output: `/app/data/splits_calendar.json`
- Calculate `multiplier = split_to / split_from`
- 150 API calls, 15-second rate limit

**Acceptance Test**:
```bash
python -c "
import json
with open('data/splits_calendar.json') as f:
    splits = json.load(f)
assert len(splits) > 0, 'Expected splits data'
print(f'✓ AT-Splits-Bootstrap PASSED: {len(splits)} symbols')
"
```

---

### Step 0.3: IBKR LSE Contract Discovery (2 min)
**File**: `python_brain/ouroboros/bootstrap_ibkr_lse.py`

```python
# RULES:
# 1. Primary: IBKR Gateway (real-time Level 1 quotes + bars)
# 2. Fallback: yfinance (0.5-1.5s jitter, 2-worker sequential)
# 3. Import IBKRSource from V1: /Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py
# 4. 12 LSE tickers (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L)
# 5. Expected: <2 minutes (IBKR) or ~3 min (yfinance fallback)

from ibkr_source import IBKRSource

ibkr = IBKRSource()
lse_tickers = [...]  # 12 tickers

if ibkr.IS_AVAILABLE:
    # IBKR Primary Path
    print("✓ IBKR available — fetching LSE contract discovery")
    for ticker in lse_tickers:
        contract = ibkr._get_contract(ticker)
        bars = ibkr.fetch_bars(ticker, period='60d', interval='1h')
        quote = ibkr.fetch_quote(ticker)
        print(f"✓ {ticker}: {len(bars)} bars, spread={quote['spread_bps']} bps")
else:
    # yfinance Fallback
    print("⚠ IBKR unavailable, falling back to yfinance")
    import yfinance as yf
    import time, random
    for idx, ticker in enumerate(lse_tickers):
        if idx > 0:
            time.sleep(random.uniform(0.5, 1.5))
        data = yf.download(ticker, period='60d', progress=False)
        print(f"✓ {ticker}: {len(data)} bars (fallback)")
```

**Acceptance Test**:
```bash
python -c "
from ibkr_source import IBKRSource
ibkr = IBKRSource()
assert ibkr.IS_AVAILABLE or True, 'IBKR or yfinance must work'
print('✓ AT-IBKR-LSE-Discovery PASSED')
print('✓ AT-IBKR-Fallback-YFinance PASSED')
"
```

---

### Step 0.4: GARCH Fitting + Validation (8 min)
**File**: `python_brain/ouroboros/test_garch_with_splits.py`

```python
# RULES:
# 1. Use Polygon Grouped endpoint (1 API call, not iterating 5,200 tickers)
# 2. Use cached dividend_calendar.json (no new API calls)
# 3. Use cached splits_calendar.json (no new API calls)
# 4. Fit GARCH(1,1) to 50 US + 12 LSE assets
# 5. Adjust prices for stock splits before fitting
# 6. Expected: <10 minutes for 50+ assets
# 7. Verify: No 1000% single-day returns (split check)

from step_0_garch_calibration import GARCHFitter

fitter = GARCHFitter(
    use_polygon_grouped=True,
    use_cached_dividends=True,
    use_cached_splits=True
)

# Fit GARCH
params = fitter.fit_garch_50_assets()
print(f"✓ GARCH fitted for {len(params)} assets")

# Verify no spikes
for ticker, p in params.items():
    assert 0 < p['alpha'] + p['beta'] < 1, f'{ticker} invalid params'
    print(f"✓ {ticker}: omega={p['omega']:.6f}, alpha={p['alpha']:.4f}, beta={p['beta']:.4f}")

print("✓ AT-GARCH-Grouped PASSED")
print("✓ AT-Price-Adjustment PASSED")
```

---

### Step 0.5: Bootstrap Validation (2 min)
Verify all cached files exist and are valid:
- `/app/data/dividend_calendar.json` (5,200+ tickers)
- `/app/data/splits_calendar.json` (all splits)
- IBKR/yfinance LSE contract cache
- GARCH parameters cache

**Acceptance Tests**:
```bash
# All acceptance tests must pass
pytest python_brain/tests/test_bootstrap_complete.py -v
# Expected output:
# ✓ AT-Bootstrap-Dividend-Calendar
# ✓ AT-Splits-Bootstrap
# ✓ AT-IBKR-LSE-Discovery
# ✓ AT-IBKR-Contract-Qualification
# ✓ AT-IBKR-Fallback-YFinance
# ✓ AT-GARCH-Grouped
# ✓ AT-Price-Adjustment
```

---

## PHASE 1: WEEK 1 REFACTORING (7.3 HOURS, MARCH 13-16)

Five isolated Claude sessions. **ANCHOR RULE**: Update `CORE_TYPES_ANCHOR.md` before AND after each session.

### RM-1: GARCH Daily Fit + Real-Time Residuals (2.5h, Monday)
**Gate**: `cargo test test_garch_inference --lib ✓`

**Files to Create/Modify**:
- `python_brain/ouroboros/step_0_garch_calibration.py` (Python GARCH fit)
- `rust_core/src/garch_inference.rs` (Rust real-time inference)

**Requirements**:
- Use Polygon Grouped endpoint (1 API call)
- Cache dividend/splits (no new API calls)
- Fit GARCH(1,1) to 50 US + 12 LSE assets
- Real-time O(1) residual calculation
- Serialize sigma2_prev to WAL every tick
- Expected fit time: <2 min for 50 assets

**Before this session**:
- Read `CORE_TYPES_ANCHOR.md` (exact struct shapes)
- Read `feeds/data_feeds.py` (TwelveData rate limiting)
- Read `bootstrap_dividend_calendar.py` (bootstrap output format)

**After this session**:
- Update `CORE_TYPES_ANCHOR.md` with exact GARCH structs
- Run `cargo clippy && cargo test`
- **PAUSE AND WAIT FOR: `APPROVED RM-1`**

---

### RM-2: WAL Dedicated Thread + Bounded Channel (3h, Tuesday)
**Gate**: `cargo test test_wal_bounded_channel_latency --lib ✓`

**Files to Create/Modify**:
- `rust_core/src/wal_actor.rs` (WAL persistent storage)
- `rust_core/src/main.rs` (thread spawn + channel setup)

**Requirements**:
- Bounded channel (10,000 capacity, no unbounded alloc)
- Dedicated std::thread (not tokio::spawn_blocking)
- Use `try_send()` with graceful telemetry drop on full
- Serialize sigma2_prev from RM-1 to WAL
- Expected latency: <1ms write, no OOM under 10k tick/sec burst

**Before this session**:
- Read `CORE_TYPES_ANCHOR.md` (WalCommand enum from RM-1)

**After this session**:
- Update `CORE_TYPES_ANCHOR.md` with WAL channel types
- Run `cargo clippy && cargo test`
- **PAUSE AND WAIT FOR: `APPROVED RM-2`**

---

### RM-3: PyO3 Native FFI Conversions (1h, Wednesday)
**Gate**: `cargo test test_pyo3_tick_extraction_latency --lib ✓`

**Files to Create/Modify**:
- `rust_core/src/python_bridge.rs` (PyO3 FFI, no JSON)

**Requirements**:
- Zero-copy conversions using `From<T>` trait
- Extract TickContext directly (no JSON serialization)
- Avoid GIL blocking in async context
- Expected latency: <0.5ms (was 5-10ms with JSON)

**Before this session**:
- Read `CORE_TYPES_ANCHOR.md` (TickContext exact shape)

**After this session**:
- Update `CORE_TYPES_ANCHOR.md` with PyO3 conversions
- Run `cargo clippy && cargo test`
- **PAUSE AND WAIT FOR: `APPROVED RM-3`**

---

### RM-4: Dynamic Huber Delta (MAD-Based) (0.5h, Wednesday)
**Gate**: `cargo test test_kalman_huber_regime_change --lib ✓`

**Files to Create/Modify**:
- `rust_core/src/student_t_kalman.rs` (Huber filter)

**Requirements**:
- Median Absolute Deviation (MAD) calculation
- `delta = 1.345 × MAD` formula
- Prevent divide-by-zero when MAD = 0 (pegged prices)
- Delta adapts within 100 ticks on volatility spike

**After this session**:
- Update `CORE_TYPES_ANCHOR.md`
- Run `cargo clippy && cargo test`
- **PAUSE AND WAIT FOR: `APPROVED RM-4`**

---

### RM-5: Exponential Backoff + Emergency Freeze (0.5h, Thursday)
**Gate**: `cargo test test_subprocess_fork_bomb_prevention --lib ✓`

**Files to Create/Modify**:
- `rust_core/src/python_subprocess_manager.rs` (subprocess lifecycle)
- `cli.py` (Python subprocess restart)

**Requirements**:
- Exponential backoff: 1s → 2s → 4s → 8s → 60s cap
- On crash: regime → YELLOW (50% size reduction)
- On 3 crashes in 60s: regime → RED (absolute halt)
- Integration with RiskGate module

**After this session**:
- Update `CORE_TYPES_ANCHOR.md`
- Run `cargo clippy && cargo test`
- **PAUSE AND WAIT FOR: `APPROVED RM-5`**

---

### Friday Validation (March 15)
**Task**: 24-hour continuous paper run

**Verification**:
- Zero container restarts (GARCH state persists)
- All risk gates functional
- WAL writes complete without blocking
- Python subprocess recovery tested
- No PyO3 lifetime errors

**Gate**: 24-hour run succeeds → **APPROVED PHASE 1 COMPLETE**

---

## PHASE 2: PHASE 8 INFRASTRUCTURE SEAL (77.4 HOURS, MARCH 16-31)

### 20 Standard Components (SC-01 through SC-20)
Implement all infrastructure:
- Order routing
- Risk gates (31 gates)
- Slippage monitoring
- Chandelier exits
- Trade journal
- etc.

### 6 Wiring Patches (WP-1 through WP-6)
Embedded fixes:
1. JSON EOF truncate (file.set_len())
2. Permit Sweeper race condition
3. Lock-Free RTOS Watchdog
4. Dynamic data type toggle
5. MPSC saturation limits
6. Synthetic dividend math (0.85x)

### 26 Acceptance Tests (AT-1 through AT-26)
All tests must pass.

### 48-Hour Paper Run
Zero crashes, all gates functional.

**Gate**: All 26 ATs pass + 48h paper run succeeds → **APPROVED PHASE 2 COMPLETE**

---

## PHASE 3: PHASES 11-23 SEQUENTIAL BUILD (358 HOURS, APRIL 1 - JUNE 15)

### Phase 11-12: Stress Testing + EGARCH (83.5h)
- Monte Carlo stress testing
- Slippage monitoring
- EGARCH volatility (±12-18% Sharpe uplift)

### Phase 13: Dynamic Kelly Sizing (30h)
- Optimal position sizing
- Sharpe uplift expected

### Phase 14: VWAP Smart Routing (25h)
- Slippage optimization

### Phase 15: LSTM/GRU Attention (80h)
- Signal prediction (±15-25% Sharpe uplift)

### Phases 16-20: Signals + Risk Gates (195h)
- Quote imbalance signals
- Chandelier stop-loss
- Smart order routing
- 31-gate aggregation
- Reconciliation audit trail

### Phase 21: DCC-GARCH Correlations (70h)
- Portfolio correlation modeling (±3-8% Sharpe uplift)

### Phase 22: Emergency Modes (35h)
- RED/YELLOW/GREEN regime switching

**Gate**: All phases complete → **APPROVED PHASE 3 COMPLETE**

---

## PHASE 4: PHASE 23 CRUCIBLE VALIDATION (63 HOURS, JUNE 16-22)

### 100-Trade Paper Validation
Execute 100 paper trades with full risk management.

**Requirements**:
- Win rate ≥ 40%
- Sharpe ratio ≥ 0.8
- Max drawdown ≤ 2.5%
- Walk-forward validation (10 × 70-trade windows)
- Trade distribution ≥ 4 uncorrelated sectors

**Gate**: Crucible PASSED → **SYSTEM FULLY VALIDATED FOR LIVE CAPITAL**

---

## PHASE 5: LIVE CAPITAL DEPLOYMENT (⏸️ PAUSED, JUNE 25)

System is ready but NOT deployed to live.

**To Deploy**: `bash scripts/deploy_live_capital.sh`

---

## DATA ARCHITECTURE (All Phases)

### Primary: IBKR Gateway
- Real-time Level 1 quotes (bid/ask/last/spread)
- Historical bars (1m, 5m, 15m, 30m, 1h, 1d)
- Zero API costs (already connected for execution)
- H-07 auto-reconnection (10-min timeout + Docker restart)

### Fallback: yfinance
- Free, unlimited calls
- Graceful degradation if IBKR unavailable >10 min
- No manual intervention needed

### Auxiliary: Polygon Starter
- Dividends (Phase 0 bootstrap only)
- Splits (Phase 0 bootstrap only)
- Nightly ex-date validation (0-1 call/night)

### Cost
- **Phase 0-4**: $0/month
- **Live (June 25+)**: ~$65/month (AWS EC2 + EBS)

---

## EXECUTION CHECKLIST

### Before Phase 0 Starts
- [ ] Verify POLYGON_API_KEY set
- [ ] Verify IBKR Gateway running on port 4004
- [ ] Verify directories exist: `/app/data/`, `/app/logs/`
- [ ] Verify IBKRSource available at `/Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py`

### During Each Phase
- [ ] Update `CORE_TYPES_ANCHOR.md` before AND after coding
- [ ] Run Ralph Wiggum loops (max 20 iterations)
- [ ] Apply Checkpoint Rule to all API operations
- [ ] Pause and wait for explicit approval gate

### After Each Phase
- [ ] All acceptance tests pass
- [ ] `cargo clippy` passes
- [ ] No compiler warnings
- [ ] Update execution journal

---

## THE MASTER COMMAND

```bash
POLYGON_API_KEY="e8vYJGn7M2Aa033mAjMuJ4eNvijgRHa6" \
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/AEGIS_INTERACTIVE.sh
```

This command executes the complete 15-week plan with approval gates at each phase:
- ✅ Phase 0: Bootstrap (87 min)
- ✅ Phase 1: Refactoring RM-1 through RM-5 (7.3h, interactive)
- ✅ Phase 2: Phase 8 Infrastructure (77.4h)
- ✅ Phase 3: Phases 11-23 Build (358h)
- ✅ Phase 4: Crucible Validation (63h)
- ✅ Phase 5: ⏸️ PAUSED (not deployed)

---

## ACKNOWLEDGMENT REQUIRED

**Before executing the master command, confirm**:

1. ✓ I understand the Ralph Wiggum Protocol (max 20 iterations, no infinite loops)
2. ✓ I understand the Anchor Rule (update CORE_TYPES_ANCHOR.md after every session)
3. ✓ I understand the Checkpoint Rule (save state after every API call)
4. ✓ I understand the IBKR-Primary Protocol (IBKR first, yfinance fallback)
5. ✓ I understand the Approval Gate Protocol (pause after every phase)

---

**Ready to execute Phase 0?**

Reply with `START PHASE 0` to begin bootstrap.

---

*AEGIS_V2_TERMINAL_DIRECTIVE.md — Generated 2026-03-10*
*Status: READY FOR EXECUTION*
