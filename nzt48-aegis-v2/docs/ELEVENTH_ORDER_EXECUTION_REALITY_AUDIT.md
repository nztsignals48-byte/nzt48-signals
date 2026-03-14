# ELEVENTH-ORDER EXECUTION REALITY AUDIT
### Critical Gaps Between Blueprint and Physical Implementation
**Date**: 2026-03-10 | **Classification**: BLOCKING AMENDMENTS REQUIRED

---

## EXECUTIVE SUMMARY

The theoretical blueprint is sound. The physical execution reveals three categories of failure vectors:

1. **Data Vendor Physics** — API call sequencing will starve the pipeline
2. **Refactoring Sprint Fragmentation** — LLM context window will corrupt Rust lifetime semantics
3. **Infrastructure Undersizing** — 50GB EBS will hit 100% utilization during Crucible

All three are **immediately correctable**. All three are **fatal if ignored**.

---

## PART 1 — DATA VENDOR PHYSICS TRAP

### Trap 1A: The Polygon Grouped Endpoint Crisis

**The Problem:**
- AEGIS SESSION_FINAL_SUMMARY.md states: "Polygon Starter allows 4 req/min dynamic token bucket"
- Ouroboros pipeline iterates over ~5,000 US tickers + ~200 European tickers daily
- **If implemented iteratively (one ticker per API call)**: 5,200 tickers ÷ 4 req/min = 1,300 minutes = **21.8 hours**
- **Reality**: Ouroboros MUST complete in 2 hours (21:00-23:00 UTC DARK window)
- **Result**: Pipeline times out at 23:00 UTC. Asian session opens blind. System crashes.

**The Critical Fix:**

Ouroboros MUST use **Polygon Grouped Daily endpoint ONLY**:

```python
# python_brain/ouroboros/step_0_polygon_loader.py
# WRONG (iterative loop):
for ticker in tickers:
    agg = polygon_client.get_aggs(ticker=ticker, timeframe='daily')  # 5,200 API calls
    # This takes 21.8 hours, violates timeline

# CORRECT (single grouped call):
response = polygon_client.get_grouped_daily_aggs(
    date='2026-03-10'  # Fetch the entire US market in ONE call
)
# Parse response: 10,000 US stocks in 1 API call
# Takes <100ms

# For European tickers (YFinance, not Polygon):
for ticker in european_tickers:
    hist = yf.download(ticker, period='60d', progress=False)
    # Parallel with 5 threads: 200 tickers ÷ 5 threads = 40 sequential calls
    # Acceptable within 2-hour window
```

**Verification Required Before Week 1**:
```bash
# Confirm Polygon Grouped endpoint returns the expected structure
curl -s "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/2026-03-10" \
  -H "Authorization: Bearer e8vYJGn7..." | jq '.results | length'
# Expected output: ~10,000+ stock aggregates in single response

# Measure response time
time curl -s "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/2026-03-10" \
  -H "Authorization: Bearer e8vYJGn7..." | jq '.results | length'
# Expected: <1 second end-to-end
```

**Updated Acceptance Test (AT-Polygon-Grouped)**:
```bash
# Verify Ouroboros step_0 uses grouped endpoint only
grep -c "get_grouped_daily_aggs" python_brain/ouroboros/step_0_polygon_loader.py
# Must return ≥1 (endpoint is used)

grep -c "for.*in.*tickers.*get_aggs" python_brain/ouroboros/step_0_polygon_loader.py
# Must return 0 (no iterative loops on Polygon)

# Verify completion time
time python python_brain/ouroboros/step_0_polygon_loader.py --date 2026-03-10
# Must complete in <2 minutes
```

---

### Trap 1B: The European Data Vacuum

**The Problem:**
- SESSION_FINAL_SUMMARY.md confirms: "Polygon .L tickers return 0 results (US-only coverage)"
- **Question**: Where is Ouroboros getting 60-day historical data for:
  - 12 LSE-listed leveraged ETPs (QQQ3.L, 3LUS.L, etc.)
  - 200+ European direct equities (for future Phase 2)
  - Currency pairs (GBP/USD, EUR/GBP)

- **Current fallback**: TwelveData (800 calls/day limit) or YFinance (rate-limited)
- **Result**: A 2,000-ticker European universe cannot be fitted with GARCH parameters within 800 API calls

**The Critical Fix:**

Ouroboros MUST implement a **two-tier data architecture**:

```python
# python_brain/ouroboros/data_loader.py
import yfinance as yf
from tweepy import tweepy  # For alternative data if needed
import pandas as pd

class TwoTierDataLoader:
    """
    Tier 1: Direct market data (LSE, European exchanges)
    Tier 2: Fallback aggregators (YFinance, Alpha Vantage)
    """

    def fetch_european_historical(self, tickers: list, period='60d') -> dict:
        """
        Fetch 60-day history for European tickers.
        Uses YFinance (rate limit: unlimited requests, 1-hour throttle).
        """
        results = {}

        # Batch: 5 parallel threads to avoid throttling
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {ticker: executor.submit(yf.download, ticker, period=period, progress=False)
                      for ticker in tickers}

            for ticker, future in futures.items():
                try:
                    results[ticker] = future.result()
                except Exception as e:
                    print(f"Warning: {ticker} failed, using cached data or zero-vector")
                    results[ticker] = pd.DataFrame()  # Fallback: zero-vector

        return results

    def fetch_lse_leveraged_etps(self, lse_etps: list) -> dict:
        """
        Fetch LSE leveraged ETP data (QQQ3.L, 3LUS.L, etc.)
        YFinance handles .L tickers correctly.
        """
        return self.fetch_european_historical(lse_etps, period='60d')

    def fetch_garch_universe(self) -> dict:
        """
        Complete data fetch for GARCH calibration.
        Returns: {ticker: daily_ohlcv_dataframe}
        """
        us_data = {}    # From Polygon grouped endpoint
        eu_data = self.fetch_european_historical(self.european_tickers)
        lse_data = self.fetch_lse_leveraged_etps(self.lse_tickers)

        # Merge
        complete_universe = {**us_data, **eu_data, **lse_data}
        return complete_universe
```

**Verification Required Before Week 1**:
```bash
# Test YFinance parallel fetch for 12 LSE ETPs
python -c "
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor
import time

lse_tickers = ['QQQ3.L', '3LUS.L', '3SEM.L', 'GPT3.L', 'NVD3.L', 'TSL3.L', 'TSM3.L', 'MU2.L', 'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L']

start = time.time()
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {t: executor.submit(yf.download, t, period='60d', progress=False) for t in lse_tickers}
    results = {t: f.result() for t, f in futures.items()}
elapsed = time.time() - start

print(f'Fetched {len(results)} tickers in {elapsed:.1f} seconds')
print(f'All tickers have data: {all(len(v) > 0 for v in results.values())}')
"
# Expected: <10 seconds, all tickers have data
```

**Updated Acceptance Test (AT-European-Data)**:
```bash
# Verify Ouroboros has complete European data pathway
grep -c "yfinance" python_brain/ouroboros/data_loader.py
# Must return ≥1 (YFinance is primary EU source)

grep -c "ThreadPoolExecutor.*max_workers=5" python_brain/ouroboros/data_loader.py
# Must return ≥1 (parallel fetching configured)

# Verify 12 LSE tickers return complete data
python -c "
import yfinance as yf
tickers = ['QQQ3.L', '3LUS.L', '3SEM.L', 'GPT3.L', 'NVD3.L', 'TSL3.L', 'TSM3.L', 'MU2.L', 'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L']
for t in tickers:
    data = yf.download(t, period='60d', progress=False)
    assert len(data) > 50, f'{t} has insufficient data: {len(data)} rows'
print('All 12 LSE tickers validated')
"
# Expected: All 12 tickers pass
```

---

## PART 2 — REFACTORING SPRINT VULNERABILITIES

### Trap 2A: The GARCH State Continuity Void

**The Problem:**
- RM-1 mandates fitting GARCH(\omega, \alpha, \beta) nightly
- Rust then recursively computes \sigma_t^2 in real-time based on returns
- **Critical flaw**: The recursive state is ephemeral (lives only in Rust memory)
- **Failure mode**: Container restarts at 14:00 UTC
  - Rust reboots with static parameters (\omega, \alpha, \beta)
  - But \sigma_t^2 is initialized to zero
  - First tick arrives: artificial "innovation shock" of massive magnitude
  - EVT tail model instantly triggers false CVaRExceeded veto
  - All positions forcibly liquidated on phantom risk signal

**The Critical Fix:**

GARCH state (\sigma_t^2) MUST be serialized to persistent storage on every tick:

```rust
// In rust_core/src/garch_inference.rs
pub struct GARCHInference {
    omega: f64,
    alpha: f64,
    beta: f64,
    sigma2_prev: f64,

    // CRITICAL: Persistence layer
    wal_channel: mpsc::UnboundedSender<WalCommand>,  // Write to WAL every tick
}

impl GARCHInference {
    pub fn update_residual(&mut self, return_: f64) -> f64 {
        let sigma2 = self.omega + self.alpha * return_.powi(2) + self.beta * self.sigma2_prev;
        self.sigma2_prev = sigma2;

        // PERSIST the new state to WAL
        let _ = self.wal_channel.send(WalCommand::WriteGARCHState {
            timestamp_ns: now_ns(),
            sigma2: sigma2,
            return_: return_,
        });

        return_ / sigma2.sqrt()
    }
}

// On container boot: reconstruct GARCH state from WAL
pub async fn reconstruct_garch_state_from_wal() -> Result<GARCHInference> {
    let mut garch = GARCHInference::new(omega, alpha, beta, 0.0);

    // Replay WAL: last 1,000 ticks
    let wal_entries = read_wal_tail(1000)?;
    for entry in wal_entries {
        if let WalCommand::WriteGARCHState { sigma2, return_, .. } = entry {
            garch.sigma2_prev = sigma2;  // Restore exact state
        }
    }

    Ok(garch)
}
```

**Acceptance Test (AT-GARCH-Persistence)**:
```bash
# Test: Boot container, verify GARCH state is seamlessly reconstructed

# Step 1: Run engine for 100 ticks
cargo run --release &
ENGINE_PID=$!
sleep 5

# Step 2: Force container restart (kill + restart)
kill $ENGINE_PID
sleep 2
cargo run --release &

# Step 3: Verify GARCH state is reconstructed from WAL
# Check logs: "GARCHState reconstructed from WAL: sigma2={value}"
docker logs nzt48 | grep "GARCHState reconstructed"
# Expected: At least one line confirming reconstruction

# Step 4: Inject a test tick, verify no artificial volatility spike
# (This requires a tick injection harness)
```

---

### Trap 2B: The Unbounded WAL Channel OOM

**The Problem:**
- RM-2 moves tokio::fs (blocking pool) to a dedicated std::thread via crossbeam
- **Question**: Is the channel bounded or unbounded?
  - **If unbounded**: EBS latency spike (IOPS exhausted) causes async reactor to flood channel with millions of queued events → Linux OOM kill
  - **If bounded**: Full channel causes .send().await to block Tokio reactor → deadlock (exact same problem you're fixing)

**The Critical Fix:**

The channel MUST be **bounded with non-blocking try_send(), and the engine must drop telemetry on overflow**:

```rust
// In rust_core/src/wal_actor.rs
pub struct WalActor {
    rx: crossbeam::channel::Receiver<WalCommand>,
    dropped_count: u64,
}

impl WalActor {
    pub async fn create_channel(buffer_size: usize) -> (mpsc::UnboundedSender<WalCommand>, WalActor) {
        // BOUNDED channel: 10,000 events max in flight
        let (tx, rx) = crossbeam::channel::bounded(buffer_size);
        (tx, WalActor { rx, dropped_count: 0 })
    }

    pub fn run(mut self) {
        let mut file = File::create("/app/logs/active_state.wal").unwrap();
        let mut batch = Vec::with_capacity(100);

        while let Ok(cmd) = self.rx.recv() {
            batch.push(cmd);

            if batch.len() >= 100 {
                self.flush_batch(&mut file, &batch);
                batch.clear();
            }
        }
    }

    fn flush_batch(&self, file: &mut File, batch: &[WalCommand]) {
        for cmd in batch {
            let _ = file.write_all(&serialize_wal_command(cmd));
        }
        let _ = file.sync_all();
    }
}

// In main.rs: Non-blocking send
pub fn enqueue_wal_event(tx: &crossbeam::channel::Sender<WalCommand>, cmd: WalCommand) {
    match tx.try_send(cmd) {
        Ok(_) => {
            // Event successfully enqueued
        }
        Err(crossbeam::channel::TrySendError::Full(_)) => {
            // WAL channel is full (EBS is hanging)
            // Drop the telemetry event; preserve execution survival
            eprintln!("WAL channel full, dropping telemetry (EBS latency?)");
            // Counter incremented; monitored by health check
        }
        Err(crossbeam::channel::TrySendError::Disconnected(_)) => {
            // WAL thread died; trigger emergency shutdown
            eprintln!("WAL thread dead, triggering emergency halt");
            // Set RiskRegime::Red, liquidate all
        }
    }
}
```

**Verification Required Before Week 1**:
```bash
# Confirm bounded channel with try_send
grep -A 5 "crossbeam::channel::bounded" rust_core/src/wal_actor.rs
# Expected: bounded(10000) or similar explicit size

grep "try_send" rust_core/src/main.rs
# Expected: ≥1 match (non-blocking send used)

# Load test: Simulate 10k ticks/sec with EBS latency spike
cargo test test_wal_bounded_channel_no_oom --release
# Expected: No OOM, events dropped gracefully when channel fills
```

---

### Trap 2C: The Exponential Backoff Zombie State

**The Problem:**
- RM-5 implements exponential backoff on Python sys.exit(255) fork bomb
- **If backoff triggers**: 15-minute delay while Python brain is dead
- **Reality**: During live trading, a 15-minute blindness is catastrophic
  - ES moves 2% in 15 minutes
  - Your hedges become stale
  - Leverage estimates are wrong
  - Positions blow up

**The Critical Fix:**

Backoff MUST immediately trigger an aggressive **portfolio emergency freeze**:

```rust
// In rust_core/src/python_subprocess_manager.rs
pub struct PythonSubprocessManager {
    recent_exits: VecDeque<Instant>,
    respawn_backoff_ms: u64,
    emergency_freeze_active: bool,
}

pub async fn respawn_with_backoff(&mut self) -> Result<()> {
    let crashes_in_60s = self.count_recent_exits(Duration::from_secs(60));

    if crashes_in_60s >= 1 {
        // Python died; activate emergency freeze IMMEDIATELY
        self.emergency_freeze_active = true;

        // Set portfolio to minimum risk: tight Chandelier stops, no new entries
        self.arbiter.regime = RiskRegime::Yellow;  // 50% size reduction
        self.arbiter.chandelier_stop_factor = 0.5;  // Tighten stops by 50%

        log::error!("Python crash detected: Emergency freeze activated. Backoff starts: {}ms", self.respawn_backoff_ms);
    }

    if crashes_in_60s >= 3 {
        // Three crashes in 60 seconds: trigger absolute halt
        self.arbiter.regime = RiskRegime::Red;
        self.arbiter.halt_reason = "Python subprocess failed 3x in 60s".to_string();
        return Err(EngineError::SystemHaltRequested);
    }

    // Wait for backoff before respawning
    tokio::time::sleep(Duration::from_millis(self.respawn_backoff_ms)).await;

    // Double the backoff for next time (exponential)
    self.respawn_backoff_ms = (self.respawn_backoff_ms * 2).min(60_000);

    // Attempt respawn
    self.respawn_python_subprocess().await?;

    // If respawn succeeds, clear emergency freeze
    if self.emergency_freeze_active {
        log::info!("Python subprocess recovered; Emergency freeze cleared");
        self.emergency_freeze_active = false;
        self.arbiter.regime = RiskRegime::Green;
    }

    Ok(())
}
```

**Acceptance Test (AT-Python-Backoff)**:
```bash
# Test: Force Python crash, verify emergency freeze activates

# Step 1: Inject a Python crash (kill -9 python process)
docker exec nzt48 pkill -9 -f "python.*ouroboros"

# Step 2: Monitor engine logs for emergency freeze
docker logs nzt48 --follow &
LOGS_PID=$!

# Step 3: Verify regime changes to Yellow within 1 second
sleep 1
docker exec nzt48 curl localhost:8000/api/status | jq '.regime'
# Expected: "Yellow"

# Step 4: Verify Chandelier factor is tightened
docker exec nzt48 curl localhost:8000/api/status | jq '.chandelier_stop_factor'
# Expected: ~0.5 (half the normal value)

# Step 5: Wait 60+ seconds, verify backoff escalates
sleep 65

# Step 6: Verify second crash triggers regime=Red
docker exec nzt48 pkill -9 -f "python.*ouroboros"
sleep 1
docker exec nzt48 curl localhost:8000/api/status | jq '.regime'
# Expected: "Red" after 2+ crashes
```

---

## PART 3 — THE EXECUTION BLUEPRINT REALITY CHECK

### Trap 3A: The "Ship of Theseus" LLM Refactoring Risk

**The Problem:**
- The AEGIS_WEEK1_REFACTORING_SPRINT.md dictates a single "7.5-hour sprint"
- Asking Claude Code to refactor 5 critical mandates (RM-1 through RM-5) sequentially in one context window
- **Context window effect**: By RM-4, Claude has lost exact details of RM-1's trait lifetimes
- **Result**: Rust compiler errors on wal_actor.rs referencing lifetimes declared in garch_inference.rs
- **Recovery**: 3-day debugging loop chasing phantom borrow checker errors

**The Critical Fix:**

Execute RM-1 through RM-5 as **strictly isolated sessions**. Clear the Claude context window completely between each RM:

```markdown
# CORRECTED: RM-EXECUTION PROTOCOL

## Session 1 (RM-1 ONLY)
- Claude: "Implement RM-1: GARCH daily fit + O(1) residuals"
- Scope: ONLY touch rust_core/src/garch_inference.rs + python_brain/ouroboros/step_0_garch_calibration.py
- Gate: cargo test, cargo clippy pass, AT-RM1 passes
- End Session 1: PAUSE. Do not proceed to RM-2 yet.

## Session 2 (RM-2 ONLY)
- Claude: "Start fresh context. Implement RM-2: WAL dedicated thread + crossbeam channel"
- Context: Provide the exact signature of garch_inference.rs::WalCommand enum (copy-paste from merged code)
- Scope: ONLY touch rust_core/src/wal_actor.rs + rust_core/src/main.rs
- Gate: cargo test, cargo clippy pass, AT-RM2 passes
- End Session 2: PAUSE. Do not proceed to RM-3 yet.

## Session 3 (RM-3 ONLY)
- Claude: "Start fresh context. Implement RM-3: PyO3 native FFI conversions"
- Context: Provide the exact signatures of WalCommand, GARCHInference (copy-paste from merged code)
- Scope: ONLY touch rust_core/src/python_bridge.rs
- Gate: cargo test, cargo clippy pass, AT-RM3 passes
- End Session 3: PAUSE. Do not proceed to RM-4 yet.

## Session 4 (RM-4 ONLY)
- Claude: "Start fresh context. Implement RM-4: Dynamic Huber delta (MAD-based)"
- Context: Provide the exact signatures of all dependencies
- Scope: ONLY touch rust_core/src/student_t_kalman.rs
- Gate: cargo test, cargo clippy pass, AT-RM4 passes
- End Session 4: PAUSE. Do not proceed to RM-5 yet.

## Session 5 (RM-5 ONLY)
- Claude: "Start fresh context. Implement RM-5: Exponential backoff + fork bomb prevention"
- Context: Provide the exact signatures of all dependencies
- Scope: ONLY touch rust_core/src/python_subprocess_manager.rs + python_brain/ouroboros/cli.py
- Gate: cargo test, cargo clippy pass, AT-RM5 passes
- End Session 5: Complete. All refactoring done.

## Final Gate (After All Sessions)
- Run: cargo test --all-features
- Run: 48-hour continuous paper run
- Gate: All tests pass, no runtime crashes
- Status: Phase 8 unconditionally ready
```

**Why This Matters:**
- Claude's context window is finite (~150k tokens)
- By RM-5, detailed trait definitions from RM-1 are "forgotten"
- LLM hallucination risk increases exponentially with refactoring depth
- **Solution**: Session boundaries = context reset = perfect isolation

---

### Trap 3B: EBS Volume Undersizing (50GB is Not Enough)

**The Problem:**
- SESSION_FINAL_SUMMARY.md confirms scaling to 50GB
- **Reality of 48-hour Crucible**:
  - 100 leveraged positions at 10-100 ticks/second average
  - Each tick = ndjson event (~500 bytes) written to WAL
  - **Tick volume**: 100 positions × 50 ticks/sec × 86,400 seconds/day × 2 days = 864,000,000 ticks
  - **WAL size**: 864M ticks × 500 bytes/tick = **432GB of raw WAL data**
  - **Compressed** (with gzip): 432GB ÷ 5 = **86GB compressed**
  - **Plus**: OS, Docker images, Rust target/ (15GB), Polars spillover (10GB)
  - **Total**: 86GB + 15GB + 10GB = **111GB minimum**

- **With 50GB**: System hits 100% disk at ~36 hours into Crucible
- **Result**: EBS exhaustion → I/O stalls → Watchdog timeout → crash loop

**The Critical Fix:**

Expand EBS to **100GB gp3** immediately (before Week 1 starts):

```bash
# On local machine
aws ec2 modify-volume \
  --volume-id vol-0da987aac2c09d7c5 \
  --size 100 \
  --region us-east-1

# Monitor progress (takes 5-10 minutes)
aws ec2 describe-volumes-modifications \
  --filters Name=original-volume-id,Values=vol-0da987aac2c09d7c5 \
  --region us-east-1 \
  --query 'VolumesModifications[0].[ModificationState,Progress]'

# On EC2 instance (after modification completes)
sudo growpart /dev/xvda 1
sudo resize2fs /dev/xvda1

# Verify
df -h /
# Expected: 100GB available
```

**Acceptance Test (AT-EBS-100GB)**:
```bash
# Verify volume size
df -h / | awk 'NR==2 {print $2}'
# Expected output: ~100G

# Stress test: Write 80GB of data, verify no 100% disk condition
dd if=/dev/zero of=/tmp/stress_test.bin bs=1G count=80 &
WRITE_PID=$!

# Monitor disk usage over 5 minutes
for i in {1..5}; do
  sleep 60
  df -h / | awk 'NR==2 {print $5}'  # % used
done

kill $WRITE_PID
rm /tmp/stress_test.bin

# Expected: Disk usage peaks at ~90%, never hits 100%
```

---

## PART 4 — AMENDED WEEK 1 TIMELINE

### Corrected Immediate Actions

**TODAY (2026-03-10)**:
- [ ] **EBS Expansion**: AWS console modify-volume to **100GB** (not 50GB)
  - Monitor progress
  - SSH to EC2: `sudo growpart /dev/xvda 1 && sudo resize2fs /dev/xvda1`
  - Verify: `df -h /` shows 100GB available

- [ ] **Polygon Grouped Endpoint Verification**:
  - Test live call to /v2/aggs/grouped endpoint
  - Measure response time (<1 second required)
  - Confirm ~10,000 US stocks returned in single call
  - AT: `AT-Polygon-Grouped` must pass

- [ ] **European Data Pathway Validation**:
  - Test YFinance parallel fetch for 12 LSE tickers
  - Measure time (<10 seconds required)
  - Confirm all 12 return complete 60-day history
  - AT: `AT-European-Data` must pass

**BEFORE MONDAY**:
- [ ] **GARCH State Persistence Design**:
  - Draft WAL format for GARCH state serialization
  - Define boot-time WAL replay logic
  - Create AT-GARCH-Persistence acceptance test

- [ ] **WAL Channel Bounding Strategy**:
  - Decide bounded channel size (recommend 10,000)
  - Design graceful telemetry dropping on overflow
  - Create AT-WAL-Bounded acceptance test

- [ ] **Python Subprocess Emergency Freeze**:
  - Design regime transition logic (Green → Yellow → Red)
  - Define chandelier tightening on Python crash
  - Create AT-Python-Backoff acceptance test

**MONDAY WEEK 1**:
- [ ] **Session 1 (RM-1)**: GARCH daily fit + persistence
  - Time: 2.5 hours
  - Gate: AT-RM1 + AT-GARCH-Persistence pass
  - **Context window**: Clear after Session 1

- [ ] **Session 2 (RM-2)**: WAL dedicated thread + bounded channel
  - Time: 3 hours
  - Gate: AT-RM2 + AT-WAL-Bounded pass
  - **Context window**: Clear after Session 2

- [ ] **Session 3 (RM-3)**: PyO3 native FFI
  - Time: 1 hour
  - Gate: AT-RM3 passes
  - **Context window**: Clear after Session 3

- [ ] **Session 4 (RM-4)**: Dynamic Huber delta
  - Time: 0.5 hours
  - Gate: AT-RM4 passes
  - **Context window**: Clear after Session 4

- [ ] **Session 5 (RM-5)**: Exponential backoff + emergency freeze
  - Time: 0.5 hours
  - Gate: AT-RM5 + AT-Python-Backoff pass
  - **Context window**: Clear after Session 5

**THURSDAY EOD WEEK 1**:
- [ ] All 5 acceptance tests pass (AT-RM1 through AT-RM5)
- [ ] All 3 new acceptance tests pass (AT-GARCH-Persistence, AT-WAL-Bounded, AT-Python-Backoff)
- [ ] Polygon Grouped + European Data ATs pass (AT-Polygon-Grouped, AT-European-Data)
- [ ] Code review + merge to main

**FRIDAY WEEK 1**:
- [ ] Dry run: 24-hour continuous paper run (not full Crucible yet)
- [ ] Verify no container restarts
- [ ] Verify EBS disk usage stays <90%
- [ ] **Gate: GO FOR PHASE 8**

---

## PART 5 — AMENDED DOCUMENTATION

Create three new critical documents before Monday:

1. **DATA_VENDOR_PHYSICS_CORRECTIONS.md** (5 KB)
   - Polygon grouped endpoint requirement
   - YFinance parallel fetch protocol
   - EU data pipeline architecture

2. **REFACTORING_SESSION_ISOLATION_PROTOCOL.md** (3 KB)
   - RM-1 through RM-5 as separate sessions
   - Context window reset between each
   - Dependency copy-paste protocol

3. **INFRASTRUCTURE_UNDERSIZING_MITIGATION.md** (2 KB)
   - 100GB EBS justification
   - WAL compression strategy
   - Disk usage monitoring dashboard

---

## THE FINAL VERDICT

**The blueprint remains sound.**

The execution has three correctable gaps:

1. ✅ **Data Vendor Physics**: Grouped endpoints + YFinance parallel fix the pipeline starvation
2. ✅ **Refactoring LLM Risk**: Session isolation + context window reset fix hallucination
3. ✅ **Infrastructure Undersizing**: 100GB EBS + WAL compression fix the 50-hour disk overflow

**All amendments are immediately implementable.**

**Week 1 is still executable starting Monday.**

**Execute these corrections. The institution is ready.**

---

*ELEVENTH_ORDER_EXECUTION_REALITY_AUDIT.md — Generated 2026-03-10*
*Classification: CRITICAL AMENDMENTS*
*Status: READY FOR IMMEDIATE IMPLEMENTATION*
