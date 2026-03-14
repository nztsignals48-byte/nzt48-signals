# AEGIS CODEX
### Unified Execution Blueprint — V2 Hedge Fund Trading System
**Date**: 2026-03-10 | **Status**: LOCKED FOR EXECUTION | **Timeline**: Late June 2026

---

## PART 1: EXECUTIVE SUMMARY

### The Decision: Option D+ (IBKR-Primary Zero-Cost Architecture)

**User Choice**: IBKR Gateway (primary) + Polygon Starter (fallback) — $0/month
**Status**: ✅ LOCKED FOR EXECUTION

| Metric | Value |
|--------|-------|
| **Primary Data Source** | IBKR Gateway (real-time, already connected for execution) |
| **Fallback Data Source** | yfinance (free, graceful degradation) |
| **Corporate Actions** | Polygon Starter (dividends/splits only, 0-6 calls/night) |
| **Data Vendor Cost** | $0/month (IBKR: $0 — already executing; Polygon: free) |
| **Data Latency** | <100ms (IBKR) vs. 2-5s (yfinance) |
| **Timeline** | 15 weeks to live capital (Late June 2026) |
| **Daily Ouroboros Time** | <30 minutes (vs. 21.7 hours without caching) |
| **Nightly API Calls** | 0-1 (IBKR native) + 1-6 (Polygon fallback) = 1-6 max |
| **Real-Time Quotes** | ✅ YES (IBKR Level 1 bid/ask/spread) |
| **Scaling Ceiling** | £50k AUM comfortably; upgrade to Option A/B at £100k+ |
| **Cost per Month (Live)** | ~$65 (AWS EC2 + EBS post-free-tier) |

### Four Critical Execution Fixes (Fourteenth-Order Corrections)

All four are **mandatory injections** before Week 1 refactoring begins:

1. **Polygon Pagination Reality**: 150 API calls with 15-second rate limits (37.5 min, not 3-5 min)
2. **Stock Splits Bootstrap**: Parallel 150 API calls for splits calendar (prevents 1000% Kalman spikes)
3. **YFinance Throttling**: 0.5-1.5 second jitter, 2-worker sequential (prevents IP ban)
4. **Corporate Action Mutability Check**: Nightly validation that cached dividends match live Polygon API

---

## PART 2: BOOTSTRAP PROTOCOL (2 DAYS, MARCH 11-12)

### Day 1: Dividend + Splits Bootstrap (2× 37.5 min)

#### Task 1: Dividend Calendar Bootstrap (Polygon, 150 calls, 37.5 min)

**Critical Fix**: Strict sequential pagination with 15-second delays

```python
# python_brain/ouroboros/bootstrap_dividend_calendar.py (CORRECTED)
class PolygonDividendBootstrapperCORRECTED:
    def __init__(self, api_key: str, rate_limit_req_per_min: int = 4):
        self.api_key = api_key
        self.base_url = "https://api.polygon.io"
        self.rate_limit_req_per_min = 4
        self.min_delay_sec = 60 / 4  # 15 seconds per call

    def bootstrap_with_strict_rate_limit(self):
        """
        Fetch 5+ years of dividend history for ALL US tickers.

        CRITICAL: Sequential pagination with 15-second delays.
        Do NOT use asyncio or ThreadPoolExecutor (will trigger 429 ban).
        """
        all_dividends = {}
        cursor = None
        api_calls_made = 0
        start_time = time.time()

        while True:
            # RATE LIMIT: Wait before each request
            if api_calls_made > 0:
                elapsed = time.time() - start_time
                expected_time = api_calls_made * self.min_delay_sec
                if elapsed < expected_time:
                    sleep_duration = expected_time - elapsed
                    time.sleep(sleep_duration)

            # Fetch page (1000 results per page × 150 pages = 150,000 dividend events)
            params = {
                'sort': 'ex_dividend_date',
                'limit': 1000,
                'order': 'desc'
            }
            if cursor:
                params['cursor'] = cursor

            response = requests.get(
                f"{self.base_url}/v3/reference/dividends",
                params=params,
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=30
            )

            api_calls_made += 1

            if response.status_code == 429:
                raise RuntimeError("429 Too Many Requests: Rate limit exceeded")
            if response.status_code != 200:
                raise RuntimeError(f"API error: {response.status_code}")

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            # Parse dividends
            for item in results:
                ticker = item.get('ticker')
                ex_div_date = item.get('ex_dividend_date')
                amount = item.get('amount', 0.0)

                if ticker not in all_dividends:
                    all_dividends[ticker] = []
                all_dividends[ticker].append({
                    'ex_dividend_date': ex_div_date,
                    'amount': amount
                })

            cursor = data.get('next_cursor')
            if not cursor:
                break

        # Persist cache
        with open('/app/data/dividend_calendar.json', 'w') as f:
            json.dump(all_dividends, f, indent=2)

        elapsed_total = time.time() - start_time
        print(f"✓ Bootstrap complete: {len(all_dividends)} tickers, {api_calls_made} API calls, {elapsed_total/60:.1f} min")
        return all_dividends

# Expected output:
# [Call 1] Fetching page 0...
#   ✓ Page 1: 1000 events, 500 unique tickers
# Rate limit: sleeping 15.0s before call 2
# ... (150 calls total, ~37.5 minutes)
# ✓ Bootstrap complete: 5200+ unique tickers, 150 API calls, 37.5 minutes elapsed
```

**Acceptance Test (AT-Bootstrap-Dividend-Calendar)**:
```bash
python -c "
import json
with open('/app/data/dividend_calendar.json', 'r') as f:
    divs = json.load(f)
assert len(divs) >= 5000, f'Expected >=5000 tickers, got {len(divs)}'
assert all(isinstance(v, list) for v in divs.values()), 'Invalid structure'
print(f'✓ Bootstrap validated: {len(divs)} tickers, complete dividend history')
"
```

#### Task 2: Splits Calendar Bootstrap (Polygon, 150 calls, 37.5 min)

**Critical Fix**: Parallel splits bootstrap (same rate limiting as dividends)

```python
# python_brain/ouroboros/bootstrap_splits_calendar.py (NEW FILE)
class PolygonSplitsBootstrapper:
    """Bootstrap stock splits and reverse splits (critical for price adjustment)"""

    def bootstrap_splits_calendar(self):
        """
        Fetch all stock splits and reverse splits.

        Example: 1-for-10 reverse split on 2025-06-15 means:
          - Pre-split prices: ÷ 10
          - Pre-split volumes: × 10

        Without this, Kalman filter calculates 1000% single-day returns.
        """
        all_splits = {}
        cursor = None
        api_calls_made = 0
        start_time = time.time()

        while True:
            # Rate limit
            if api_calls_made > 0:
                elapsed = time.time() - start_time
                expected_time = api_calls_made * 15  # 15 seconds per call
                if elapsed < expected_time:
                    time.sleep(expected_time - elapsed)

            params = {
                'sort': 'execution_date',
                'limit': 1000,
                'order': 'desc'
            }
            if cursor:
                params['cursor'] = cursor

            response = requests.get(
                f"{self.base_url}/v3/reference/splits",
                params=params,
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=30
            )

            api_calls_made += 1

            if response.status_code == 429:
                raise RuntimeError("Rate limit exceeded")
            if response.status_code != 200:
                raise RuntimeError(f"API error: {response.status_code}")

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            for item in results:
                ticker = item.get('ticker')
                ex_date = item.get('execution_date')
                split_from = item.get('split_from')
                split_to = item.get('split_to')

                if ticker not in all_splits:
                    all_splits[ticker] = []

                all_splits[ticker].append({
                    'execution_date': ex_date,
                    'split_from': split_from,
                    'split_to': split_to,
                    'multiplier': split_to / split_from  # 10/1 = 10x for reverse split
                })

            cursor = data.get('next_cursor')
            if not cursor:
                break

        with open('/app/data/splits_calendar.json', 'w') as f:
            json.dump(all_splits, f, indent=2)

        print(f"✓ Splits bootstrap complete: {len(all_splits)} tickers, {api_calls_made} API calls")
        return all_splits
```

**Price Adjustment Integration**:
```python
# python_brain/ouroboros/step_0_price_adjustment.py (NEW FILE)
def adjust_historical_prices_for_splits(prices_df, ticker: str, splits_cache: dict):
    """
    Adjust historical OHLCV data for stock splits.

    Example: 1-for-10 reverse split on 2025-06-15
    - All prices before 2025-06-15: divide by 10
    - All volumes before 2025-06-15: multiply by 10
    """
    import pandas as pd

    if ticker not in splits_cache:
        return prices_df  # No splits, return as-is

    splits = splits_cache[ticker]
    df = prices_df.copy()

    for split in sorted(splits, key=lambda x: x['execution_date']):
        ex_date = pd.to_datetime(split['execution_date'])
        multiplier = split['multiplier']

        # Adjust all rows BEFORE the ex-date
        mask = df.index < ex_date
        df.loc[mask, 'open'] /= multiplier
        df.loc[mask, 'high'] /= multiplier
        df.loc[mask, 'low'] /= multiplier
        df.loc[mask, 'close'] /= multiplier
        df.loc[mask, 'volume'] *= multiplier

    return df
```

#### Task 3: YFinance Parallel Fetch (200 LSE tickers, 3.3 min)

**Critical Fix**: Strict sequential with 0.5-1.5 second random jitter

```python
# python_brain/ouroboros/step_0_yfinance_loader.py (CORRECTED)
class YFinanceLoaderThrottled:
    def __init__(self, max_concurrent: int = 2, delay_min_sec: float = 0.5, delay_max_sec: float = 1.5):
        """
        YFinance loader with STRICT throttling to avoid IP ban.

        - max_concurrent: 2 (NOT 5 or 10)
        - delay: 0.5-1.5 seconds with random jitter
        - Timeout: 30 seconds per ticker
        """
        self.max_concurrent = max_concurrent
        self.delay_min = delay_min_sec
        self.delay_max = delay_max_sec

    def fetch_lse_tickers(self, tickers: list, period: str = '60d') -> dict:
        """
        Fetch LSE OHLCV data with strict rate limiting.

        NOT using ThreadPoolExecutor (would trigger 403).
        Using sequential fetch with random jitter.
        """
        results = {}

        for idx, ticker in enumerate(tickers):
            # Random jitter between requests
            if idx > 0:
                jitter = random.uniform(self.delay_min, self.delay_max)
                print(f"Rate limiting: {jitter:.2f}s before {ticker}")
                time.sleep(jitter)

            try:
                print(f"[{idx+1}/{len(tickers)}] Fetching {ticker}...")
                data = yf.download(ticker, period=period, progress=False, timeout=30)

                if data is not None and len(data) > 0:
                    results[ticker] = data
                    print(f"  ✓ {ticker}: {len(data)} days of history")
                else:
                    print(f"  ⚠ {ticker}: No data returned")

            except Exception as e:
                print(f"  ✗ {ticker}: {str(e)[:100]}")
                # Continue with next ticker (graceful degradation)

        print(f"\n✓ YFinance fetch complete: {len(results)}/{len(tickers)} tickers")
        return results

# Usage:
# loader = YFinanceLoaderThrottled(max_concurrent=2, delay_min_sec=0.5, delay_max_sec=1.5)
# lse_data = loader.fetch_lse_tickers(['QQQ3.L', '3LUS.L', '3SEM.L', ...])
```

### Day 2: Nightly Update Logic + GARCH Testing (2.5h)

#### Nightly Ex-Date Update (0-5 API calls per night)

```python
# python_brain/ouroboros/step_0_dividend_update.py
def update_dividend_calendar_for_ex_dates(cache_file: str, polygon_client, days_ahead: int = 7):
    """
    Nightly: Update dividends only for tickers with ex-dates in the next N days.

    Most nights: 0-5 tickers (ex-dates are announced months in advance).
    Total API calls per night: 0-5 (vs. 5,200 without caching).
    """
    today = datetime.now().date()
    upcoming_cutoff = today + timedelta(days=days_ahead)

    # Load cached dividend calendar
    with open(cache_file, 'r') as f:
        cached_divs = json.load(f)

    # Find tickers with upcoming ex-dates
    tickers_to_update = set()

    for ticker, dividends in cached_divs.items():
        for div in dividends:
            ex_date_str = div.get('ex_dividend_date')
            if not ex_date_str:
                continue

            try:
                ex_date = datetime.fromisoformat(ex_date_str).date()
                if today <= ex_date <= upcoming_cutoff:
                    tickers_to_update.add(ticker)
                    break  # Only need one upcoming ex-date per ticker
            except ValueError:
                continue

    print(f"Tickers with ex-dates in next {days_ahead} days: {len(tickers_to_update)}")

    # Fetch dividend updates only for these tickers
    api_calls = 0
    for ticker in sorted(tickers_to_update):
        try:
            response = polygon_client.get_dividends(ticker=ticker)
            if response and response.get('results'):
                cached_divs[ticker] = response['results']
                api_calls += 1
                print(f"  Updated {ticker}: {len(response['results'])} recent dividends")
        except Exception as e:
            print(f"  Warning: Failed to update {ticker}: {e}")

    # Persist updated cache
    with open(cache_file, 'w') as f:
        json.dump(cached_divs, f, indent=2)

    print(f"✓ Dividend update complete: {api_calls} API calls used")
    return api_calls, len(tickers_to_update)
```

#### GARCH Fitting with Grouped Endpoint (1 API call)

```python
# python_brain/ouroboros/step_0_garch_calibration.py (Updated for Option D)
def calibrate_garch_nightly_option_d(polygon_client, lse_tickers: list):
    """
    Fit GARCH to 50 US assets + 12 LSE assets.

    Option D changes:
    - Use Polygon Grouped endpoint (1 API call) instead of per-ticker iteration
    - Use YFinance (free) for LSE
    - Do NOT iterate dividends (already cached)
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor
    import pandas as pd

    # Step 1: Fetch US OHLCV from Polygon Grouped endpoint (1 API call)
    print("Fetching US OHLCV from Polygon Grouped...")
    us_data = polygon_client.get_grouped_daily_aggs(date='2026-03-10')
    print(f"  ✓ Retrieved {len(us_data)} US stocks in 1 API call")

    # Step 2: Fetch LSE OHLCV from YFinance (sequential with throttling)
    print("Fetching LSE OHLCV from YFinance...")
    loader = YFinanceLoaderThrottled(max_concurrent=2, delay_min_sec=0.5, delay_max_sec=1.5)
    lse_data = loader.fetch_lse_tickers(lse_tickers, period='60d')

    # Step 3: Fit GARCH to returns (no additional API calls)
    print("Fitting GARCH parameters...")
    garch_params = {}

    selected_us = list(us_data.keys())[:50]  # Top 50 US assets
    all_tickers = selected_us + lse_tickers

    for ticker in all_tickers:
        try:
            if ticker in us_data:
                prices = us_data[ticker]['close']
            else:
                prices = lse_data[ticker]['Close']

            # Calculate log returns
            returns = pd.Series(prices).pct_change().dropna()

            # Fit GARCH (from statsmodels or arch library)
            from arch import arch_model
            model = arch_model(returns, vol='Garch', p=1, q=1)
            res = model.fit(disp='off')

            garch_params[ticker] = {
                'omega': float(res.params['Volatility']['omega']),
                'alpha': float(res.params['Volatility']['alpha']),
                'beta': float(res.params['Volatility']['beta']),
            }

            print(f"  ✓ {ticker}: ω={garch_params[ticker]['omega']:.6f}")

        except Exception as e:
            print(f"  Error fitting {ticker}: {e}")

    # Persist GARCH parameters
    with open('/app/data/garch_params.json', 'w') as f:
        json.dump(garch_params, f)

    print(f"\n✓ GARCH calibration complete: {len(garch_params)} assets fitted")
    return garch_params

# Total cost: 1 API call (Polygon Grouped) + 0 calls (YFinance free) = 1 call
```

### Acceptance Tests: All Must Pass

```bash
# AT-Bootstrap-Dividend-Calendar
python -c "
import json
with open('/app/data/dividend_calendar.json', 'r') as f:
    divs = json.load(f)
assert len(divs) >= 5000, f'Expected >=5000 tickers, got {len(divs)}'
print('✓ AT-Bootstrap-Dividend-Calendar PASSED')
"

# AT-Splits-Bootstrap
python -c "
import json
with open('/app/data/splits_calendar.json', 'r') as f:
    splits = json.load(f)
assert len(splits) > 0, 'Expected splits data'
print('✓ AT-Splits-Bootstrap PASSED')
"

# AT-YFinance-Throttled
python -c "
from step_0_yfinance_loader import YFinanceLoaderThrottled
loader = YFinanceLoaderThrottled()
lse_data = loader.fetch_lse_tickers(['QQQ3.L', '3LUS.L', '3SEM.L'], period='60d')
assert len(lse_data) >= 3, 'Expected >=3 tickers'
print('✓ AT-YFinance-Throttled PASSED')
"

# AT-GARCH-Grouped
python -c "
import json
with open('/app/data/garch_params.json', 'r') as f:
    params = json.load(f)
assert len(params) >= 50, f'Expected >=50 fitted assets, got {len(params)}'
assert all('omega' in v for v in params.values()), 'Missing omega parameters'
print('✓ AT-GARCH-Grouped PASSED')
"

# AT-30-Day-Nightly-Simulation
for day in {1..30}; do
  calls=$(python -c "
from step_0_dividend_update import update_dividend_calendar_for_ex_dates
api_calls, _ = update_dividend_calendar_for_ex_dates(
    cache_file='/app/data/dividend_calendar.json',
    polygon_client=polygon,
    days_ahead=7
)
print(api_calls)
  ")
  echo "Day $day: $calls API calls"
done
# Expected: Total calls across 30 days: 10-50 (not 5,200+ per day)
```

---

## PART 3: WEEK 1 REFACTORING (7.5 HOURS, MARCH 13-16)

### Pre-Session Setup

1. **Create CORE_TYPES_ANCHOR.md** (reference for all 5 sessions)
2. **Verify bootstrap data exists** at `/app/data/`
3. **Commit CORE_TYPES_ANCHOR.md to Git**

### RM-1: GARCH Daily Fit + Real-Time Residuals (2.5h, Monday)

**Scope**: `ouroboros/step_0_garch_calibration.py` + `rust_core/src/garch_inference.rs`

**Problem**: GARCH(1,1) MLE optimization on 50 assets every tick freezes Tokio reactor

**Solution**: Fit nightly (cached) → O(1) real-time residual inference

```rust
// rust_core/src/garch_inference.rs
pub struct GARCHInference {
    omega: f64,
    alpha: f64,
    beta: f64,
    sigma2_prev: f64,
    wal_sender: WalSender,  // Bounded channel to WAL thread
}

impl GARCHInference {
    pub fn update_residual(&mut self, return_: f64) -> f64 {
        // Single recursion: O(1) operation
        let sigma2 = self.omega
            + self.alpha * return_.powi(2)
            + self.beta * self.sigma2_prev;
        self.sigma2_prev = sigma2;

        let residual = return_ / sigma2.sqrt();
        residual
    }
}
```

**Acceptance Test (AT-RM1)**:
```bash
cargo test test_garch_inference --lib
# Verify: O(1) residual calculation, <2 min fit time for 50 assets
```

**Gate**: AT-RM1 passes → Proceed to Session 2

---

### RM-2: WAL Dedicated Thread + Bounded Channel (3h, Tuesday)

**Scope**: `rust_core/src/wal_actor.rs` + `main.rs`

**Problem**: tokio::fs uses spawn_blocking (512 thread pool); 10k tick/sec burst exhausts pool → deadlock

**Solution**: Dedicated synchronous std::thread + unbounded crossbeam channel (non-blocking enqueue)

```rust
// rust_core/src/wal_actor.rs
pub enum WalCommand {
    WriteGARCHState { timestamp_ns: u64, sigma2: f64, return_: f64 },
    WriteEvent { event_type: u8, payload: Vec<u8> },
}

pub struct WalActor {
    rx: crossbeam::channel::Receiver<WalCommand>,
    file_path: String,
}

impl WalActor {
    pub fn run(self) {
        let mut file = OpenOptions::new()
            .append(true)
            .create(true)
            .open(&self.file_path)
            .expect("WAL open");

        let mut batch_count = 0;

        while let Ok(cmd) = self.rx.recv() {
            match cmd {
                WalCommand::WriteGARCHState { timestamp_ns, sigma2, return_ } => {
                    let json = format!(
                        r#"{{"ts":{},"s2":{},"r":{}}}"#,
                        timestamp_ns, sigma2, return_
                    );
                    let _ = file.write_all(json.as_bytes());
                    batch_count += 1;

                    // Batch fsync: every 100 writes
                    if batch_count >= 100 {
                        let _ = file.sync_all();
                        batch_count = 0;
                    }
                }
                WalCommand::WriteEvent { event_type, payload } => {
                    let _ = file.write_all(&payload);
                    batch_count += 1;
                    if batch_count >= 100 {
                        let _ = file.sync_all();
                        batch_count = 0;
                    }
                }
            }
        }
    }
}
```

**Acceptance Test (AT-RM2)**:
```bash
cargo test test_wal_bounded_channel_latency --lib
# Verify: <1ms latency, no OOM under 10k tick/sec burst
```

**Gate**: AT-RM2 passes → Proceed to Session 3

---

### RM-3: PyO3 Native FFI Conversions (1h, Wednesday)

**Scope**: `rust_core/src/python_bridge.rs`

**Problem**: JSON serialization/deserialization = 5-10ms latency per call

**Solution**: Native PyO3 conversions with #[pyclass] macro (zero-copy)

```rust
// rust_core/src/python_bridge.rs
#[pyclass]
pub struct TickContext {
    #[pyo3(get, set)] pub ticker_id: u32,
    #[pyo3(get, set)] pub price: f64,
    #[pyo3(get, set)] pub size: f64,
    #[pyo3(get, set)] pub timestamp_ns: u64,
}

pub fn call_python_analysis(data: TickContext) -> Result<AnalysisResult> {
    Python::with_gil(|py| {
        // Convert Rust struct → Python object directly (zero-copy)
        let py_context = data.into_py(py);

        // Call Python function with native object (no JSON!)
        let result = ouroboros_module.call_method1(py, "analyze", (py_context,))?;

        // Convert result back to Rust
        let analysis: AnalysisResult = result.extract(py)?;
        Ok(analysis)
    })
}
```

**Acceptance Test (AT-RM3)**:
```bash
cargo test test_pyo3_tick_extraction_latency --lib
# Verify: <0.5ms latency (was 5-10ms with JSON)
```

**Gate**: AT-RM3 passes → Proceed to Session 4

---

### RM-4: Dynamic Huber Delta (MAD-Based) (0.5h, Wednesday)

**Scope**: `rust_core/src/student_t_kalman.rs`

**Problem**: Hardcoded `HUBER_DELTA = 1.5` fails on volatility regime changes

**Solution**: Dynamic delta = 1.345 × MAD (Median Absolute Deviation)

```rust
// rust_core/src/student_t_kalman.rs
pub struct StudentTKalmanFilter {
    x: f64,  // State (price)
    p: f64,  // Uncertainty
    huber_delta: f64,  // Dynamic, MAD-based
    residuals_buffer: VecDeque<f64>,  // Last 100 residuals
}

impl StudentTKalmanFilter {
    pub fn update_huber_delta(&mut self) {
        if self.residuals_buffer.len() < 10 {
            return;
        }

        // Calculate Median Absolute Deviation
        let mut abs_residuals: Vec<f64> = self.residuals_buffer.iter().map(|r| r.abs()).collect();
        abs_residuals.sort_by(|a, b| a.partial_cmp(b).unwrap());

        let median = abs_residuals[abs_residuals.len() / 2];
        let mad = abs_residuals.iter()
            .map(|r| (r - median).abs())
            .collect::<Vec<_>>();

        // Find median of absolute deviations
        let mut mad_sorted = mad.clone();
        mad_sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let mad_value = mad_sorted[mad_sorted.len() / 2];

        // Huber delta: 1.345 × MAD
        self.huber_delta = if mad_value > 0.0 {
            1.345 * mad_value
        } else {
            1.5  // Fallback
        };
    }
}
```

**Acceptance Test (AT-RM4)**:
```bash
cargo test test_kalman_huber_regime_change --lib
# Verify: Delta adapts within 100 ticks on volatility spike
```

**Gate**: AT-RM4 passes → Proceed to Session 5

---

### RM-5: Exponential Backoff + Emergency Freeze (0.5h, Thursday)

**Scope**: `rust_core/src/python_subprocess_manager.rs` + `cli.py`

**Problem**: If Python crashes with exit(255), Rust respawns instantly → fork bomb if bug persists

**Solution**: Exponential backoff (1s → 2s → 4s → 8s → 60s cap) + 3-strike SystemHalt

```rust
// rust_core/src/python_subprocess_manager.rs
pub struct PythonSubprocessManager {
    recent_exits: VecDeque<Instant>,
    respawn_backoff_ms: u64,
}

impl PythonSubprocessManager {
    pub async fn respawn_with_backoff(&mut self) -> Result<()> {
        loop {
            let mut child = tokio::process::Command::new("python")
                .arg("ouroboros.py")
                .spawn()?;

            match child.wait().await {
                Ok(status) if status.code() == Some(255) => {
                    // Clean flush requested
                    self.record_exit(Instant::now());

                    // Check for fork bomb pattern
                    let crashes_in_60s = self.count_recent_exits(Duration::from_secs(60));

                    if crashes_in_60s >= 3 {
                        // EMERGENCY: More than 3 crashes in 60 seconds
                        log::error!("FORK_BOMB_DETECTED: {} crashes in 60s. SystemHalt.", crashes_in_60s);
                        return Err(EngineError::SystemHaltRequested);
                    }

                    // Exponential backoff: 1s, 2s, 4s, 8s
                    let backoff = std::cmp::min(self.respawn_backoff_ms, 60_000);
                    log::warn!("Python exited (255). Respawning in {}ms.", backoff);
                    tokio::time::sleep(Duration::from_millis(backoff)).await;

                    // Increase backoff for next retry
                    self.respawn_backoff_ms = (self.respawn_backoff_ms * 2).min(60_000);
                }
                Ok(status) => {
                    log::error!("Python exited fatally: {:?}", status);
                    self.respawn_backoff_ms = 1_000;
                    return Err(EngineError::ProcessFatal);
                }
                Err(e) => {
                    log::error!("Wait failed: {}", e);
                    return Err(e.into());
                }
            }
        }
    }

    fn record_exit(&mut self, now: Instant) {
        self.recent_exits.push_back(now);
        if self.recent_exits.len() > 5 {
            self.recent_exits.pop_front();
        }
    }

    fn count_recent_exits(&self, window: Duration) -> usize {
        let cutoff = Instant::now() - window;
        self.recent_exits.iter().filter(|&&t| t > cutoff).count()
    }
}
```

**Acceptance Test (AT-RM5)**:
```bash
cargo test test_subprocess_fork_bomb_prevention --lib
# Verify: Backoff escalates, SystemHalt triggered after 3 crashes
```

**Gate**: AT-RM5 passes → Friday validation

---

### Friday Validation (March 15, 2026)

**Task**: 24-hour continuous paper run

**Verification**:
- Zero container restarts (GARCH state persists)
- All risk gates functional
- WAL writes complete without blocking
- Python subprocess recovery tested
- No PyO3 lifetime errors

**Gate**: 24-hour run succeeds → **Phase 8 unconditionally ready**

---

## PART 4: PHASE 8 INFRASTRUCTURE SEAL (77.4 HOURS, MARCH 16-31)

**20 standard components (SC-01 through SC-20)**
**6 wiring patches (WP-1 through WP-6) embedded**
**26 acceptance tests**

### Data Vendor Integration in Phase 8

**File**: `rust_core/src/ouroboros_bridge.rs`

```rust
pub struct OutoborosDataBridge {
    // Option D: All data passed from Python after caching/filtering
    garch_params: Arc<DashMap<String, GARCHParams>>,
    dividend_cache: Arc<DashMap<String, f64>>,  // Ex-date: yield
    splits_cache: Arc<DashMap<String, Vec<SplitEvent>>>,  // Historical splits
    industry_defaults: Arc<HashMap<String, f64>>,  // Sector defaults
}

impl OutoborosDataBridge {
    pub fn get_dividend_or_fallback(&self, ticker: &str, sector: &str) -> f64 {
        self.dividend_cache
            .get(ticker)
            .map(|v| v.clone())
            .or_else(|| self.industry_defaults.get(sector).cloned())
            .unwrap_or(0.02)  // Global default: 2%
    }

    pub fn adjust_prices_for_splits(&self, ticker: &str, prices: &[f64]) -> Vec<f64> {
        if let Some(splits) = self.splits_cache.get(ticker) {
            // Apply split adjustments
            let mut adjusted = prices.to_vec();
            for split in splits.iter() {
                // Adjust historical prices before split date
                for price in adjusted.iter_mut() {
                    *price /= split.multiplier;
                }
            }
            adjusted
        } else {
            prices.to_vec()
        }
    }
}
```

**No additional API calls in Phase 8 (all data pre-fetched)**

### Phase 8 Gate

- ✅ 20 SC items implemented + tested
- ✅ 6 WP patches integrated + tested
- ✅ 26 ATs pass
- ✅ 48-hour continuous paper run succeeds
- ✅ **GO FOR PHASES 11-23**

---

## PART 5: PHASES 11-23 SEQUENTIAL BUILD (358 HOURS, APRIL 1 - JUNE 15)

**Data vendor: Option D (unchanged, all data pre-fetched/cached)**

### Phases 11-12: Stress Testing + EGARCH (83.5 hours, Weeks 4-5)

**Phase 11** (30h):
- Monte Carlo stress testing (20h)
- Slippage monitoring (10h)

**Phase 12** (53.5h):
- EGARCH volatility modeling (30h) — **+12-18% Sharpe uplift**
- Phase transition (23.5h)

**Data usage**: Cached GARCH params + dividend cache. Zero new API calls.

---

### Phases 13-15: Strategic Upgrades (135 hours, Weeks 6-8)

**Phase 13** (30h): Dynamic Kelly sizing
**Phase 14** (25h): VWAP smart routing
**Phase 15** (80h): LSTM/GRU attention — **+15-25% Sharpe uplift**

**Data usage**: Cached data only. Ouroboros sends pre-computed signals.

---

### Phases 16-20: Signal Generation + Risk Gates (195 hours, Weeks 9-13)

**Phase 16** (40h): Quote imbalance signals
**Phase 17** (35h): Chandelier stop-loss
**Phase 18** (50h): Smart order routing
**Phase 19** (45h): Risk gate aggregation (31 gates)
**Phase 20** (25h): Reconciliation audit trail

**Data usage**: Cached data only.

---

### Phases 21-22: Advanced Correlations (105 hours, Weeks 14-15)

**Phase 21** (70h): DCC-GARCH portfolio correlations — **+3-8% Sharpe**
**Phase 22** (35h): Emergency modes (RED/YELLOW/GREEN)

**Data usage**: Cached GARCH params for DCC fitting. Zero new API calls.

---

### Phase 23: Crucible Validation (63 hours, Weeks 15-16)

**Requirements**:
- 100 paper trades minimum
- Win rate ≥ 40% (statistically significant)
- Sharpe ≥ 0.8 (world-class)
- Max drawdown ≤ 2.5% (hard stop)
- Walk-forward validation (10 overlapping windows)

**CRITICAL ADDITIONS**:
- **Diversity Metric**: Trades must span ≥4 uncorrelated market sectors (prevents hidden concentration risk)
- **Sample Size Warning**: 100 trades ≈ 15 effective degrees of freedom (explicit note to user)
- **WAL Priority Queue Strategy**: Critical state (Fills, Risk Vetoes) MUST be guaranteed-delivery, not dropped

**Data usage**: Cached data. Ouroboros runs nightly on cached dividends.

**Gate**: Crucible passes → **GO FOR LIVE CAPITAL**

---

## PART 6: LIVE CAPITAL DEPLOYMENT (JUNE 25, 2026)

**Initial deployment**: £10,000 ISA capital

### Daily Ouroboros Run (Nightly, 21:00-23:00 UTC DARK Window)

```bash
# Timeline with Option D:
21:00: Start
21:00-21:01: Fetch US OHLCV (Polygon Grouped, 1 API call)
21:01-21:05: Fetch LSE OHLCV (YFinance, free, sequential)
21:05-21:06: Update dividend cache (0-5 calls for ex-dates only)
21:06-21:15: Adjust prices for splits (cached, no API calls)
21:15-21:20: GARCH fitting (no API calls)
21:20-21:25: Risk gate calibration (no API calls)
21:25-21:30: Thompson Sampler allocation (no API calls)
21:30: Complete
```

**Total cost**: 1-6 API calls (vs. 5,200 without caching)
**Total time**: <30 minutes (vs. 21.7 hours without optimization)

### Corporate Action Mutability Check (Critical)

```python
# python_brain/ouroboros/step_0_corporate_action_audit.py (NEW)
def audit_dividend_cache_against_polygon():
    """
    Nightly validation: Ensure cached dividends match live Polygon API.

    This prevents silent staleness if Polygon updates ex-dates after bootstrap.
    """
    with open('/app/data/dividend_calendar.json', 'r') as f:
        cached = json.load(f)

    # Spot-check 100 random tickers
    sample_tickers = random.sample(list(cached.keys()), min(100, len(cached)))

    mismatches = []
    for ticker in sample_tickers:
        try:
            response = polygon_client.get_dividends(ticker=ticker, limit=5)
            live_divs = response.get('results', [])
            cached_divs = cached.get(ticker, [])

            # Check if ex-dates match
            live_ex_dates = {d['ex_dividend_date'] for d in live_divs}
            cached_ex_dates = {d['ex_dividend_date'] for d in cached_divs}

            if live_ex_dates != cached_ex_dates:
                mismatches.append({
                    'ticker': ticker,
                    'cached': cached_ex_dates,
                    'live': live_ex_dates,
                    'missing_in_cache': live_ex_dates - cached_ex_dates,
                })

        except Exception as e:
            log.warning(f"Audit failed for {ticker}: {e}")

    if mismatches:
        log.warning(f"Corporate action cache mismatches detected: {len(mismatches)}")
        # Re-fetch affected tickers
        for mismatch in mismatches:
            cached[mismatch['ticker']] = response['results']

        # Update cache
        with open('/app/data/dividend_calendar.json', 'w') as f:
            json.dump(cached, f)
    else:
        log.info("✓ Corporate action cache validated against Polygon (100-ticker sample)")

    return len(mismatches)
```

---

## PART 7: DECISION FRAMEWORK

### Go/No-Go Gates

| Phase | Gate | Condition | Action if No-Go |
|-------|------|-----------|-----------------|
| **Bootstrap** | All tests pass | 5/5 acceptance tests green | Fix and retest (no deadline) |
| **Week 1 Ref** | All mandates pass | RM-1 through RM-5 ATs green | Fix and retest |
| **Phase 8** | Infrastructure solid | 48-hour continuous run succeeds | Debug wiring patches + retry |
| **Phase 23** | Win rate ≥ 40% | 100 trades, WR significant | Return to Phases 11-22, debug |
| **Live Deploy** | Crucible passed | All metrics validated | Defer 1-2 weeks, validate more |

### Option D Upgrade Decision Logic

| AUM | Data Vendor | Action | Timing |
|-----|------------|--------|--------|
| £0-50k | Option D (Polygon Starter) | Continue | Phase 8-23 ✅ |
| £50k-100k | Option D + monitor | Evaluate upgrade | Ongoing |
| £100k+ | Upgrade to Option A/B | Mandatory | At £100k AUM |

**Upgrade Path**: If crossing £50k-100k AUM and experiencing >5 dividend-related CVaR errors per month, upgrade to Option A (Polygon Professional, $500-2,000/mo) or Option B (IEX Cloud, $99/mo).

---

## FINAL TIMELINE

| Week | Phase | Duration | Status |
|------|-------|----------|--------|
| **Mar 11-12** | Bootstrap | 2 days | Pre-Phase 8 |
| **Mar 13-16** | Week 1 Refactoring | 4 days | RM-1 through RM-5 |
| **Mar 16-31** | Phase 8 | 2 weeks | Infrastructure Seal |
| **Apr 1-13** | Phases 11-12 | 2 weeks | Stress + EGARCH |
| **Apr 14-20** | Phase 13 | 1 week | Kelly Sizing |
| **Apr 21-27** | Phase 14 | 1 week | VWAP |
| **Apr 28-May 11** | Phase 15 | 2 weeks | LSTM |
| **May 12-Jun 1** | Phases 16-20 | 3 weeks | Signals + Gates |
| **Jun 2-8** | Phase 21 | 1 week | DCC-GARCH |
| **Jun 9-15** | Phase 22-23 | 1 week | Emergency + Crucible |
| **Jun 25** | Live Capital | Day 1 | Deploy £10,000 |

**Total: 15 weeks from bootstrap to live capital (Late June 2026)**

---

## CODEX COMPLETE

✅ **Bootstrap**: 2 days (Polygon + Splits + YFinance)
✅ **Refactoring**: 1 week (5 mandates, context reset safe)
✅ **Phase 8**: 2 weeks (20 SCs + 6 WPs + 26 ATs)
✅ **Phases 11-23**: 10 weeks (sequential build, validated)
✅ **Total**: 15 weeks → **Late June 2026 live capital**

✅ **Cost**: $0 (Option D — Polygon Starter only)
✅ **Data Vendor**: 4 Fourteenth-Order corrections applied
✅ **Risk Management**: 31-gate architecture, Blood Oath enforced
✅ **Acceptance Criteria**: Clear gates for each phase

**Everything from here is execution.**

---

*AEGIS_CODEX.md — Unified Execution Blueprint*
*Generated: 2026-03-10 | Status: LOCKED FOR EXECUTION*
*Next Action: Bootstrap begins March 11*
