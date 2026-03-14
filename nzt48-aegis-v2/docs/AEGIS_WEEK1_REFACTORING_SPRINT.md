# AEGIS WEEK 1 REFACTORING SPRINT
### 5 Critical Mandates — Ninth-Order Traps Sealed
**Date**: 2026-03-10 | **Duration**: 7.5 hours | **Status**: BLOCKING PHASE 8

> This document specifies the exact refactoring mandates that MUST be completed before Phase 8 kickoff. These are not optional enhancements; they are CRITICAL fixes for CPU complexity, numerical stability, and I/O blocking traps that will cause silent failures or complete system freezes during live trading.

---

## MANDATE SUMMARY

| ID | Trap | File | Fix | Effort | Blocker |
|----|------|------|-----|--------|---------|
| **RM-1** | GARCH CPU choke | `ouroboros/garch_model.py` | Don't optimize daily; pass params to Rust | 2.5 hours | **CRITICAL** |
| **RM-2** | tokio::fs blocking pool death spiral | `rust_core/src/wal_writer.rs` | Dedicated sync thread + crossbeam channel | 3 hours | **CRITICAL** |
| **RM-3** | PyO3 FFI JSON overhead | `python_bridge.rs` | Use native PyAny::extract conversion | 1 hour | **HIGH** |
| **RM-4** | Huber loss static delta | `rust_core/src/student_t_kalman.rs` | Dynamic $\delta = 1.345 × MAD$ | 0.5 hours | **MEDIUM** |
| **RM-5** | sys.exit(255) fork bomb | `python_brain/ouroboros/cli.py` + `command_wrapper.rs` | Respawn backoff + SystemHalt | 0.5 hours | **MEDIUM** |

**Total: 7.5 hours (exact match to refactoring budget)**

---

## MANDATE 1: GARCH CPU CHOKE (RM-1)

### The Problem

**Current (BROKEN):**
```python
# In ouroboros/garch_model.py (fictional, needs verification)
for ticker in all_50_tickers:
    garch = GARCH11Model(returns=ticker_returns)
    params = garch.fit()  # Non-linear MLE optimization
    residuals = garch.standardized_residuals(params)
    evt_tail = fit_gpd(residuals)  # EVT applied to residuals
```

**Issue:**
- GARCH(1,1) fitting uses Maximum Likelihood Estimation (MLE)
- MLE requires iterative non-linear optimization (L-BFGS, Nelder-Mead)
- Running on 50 assets concurrently → 50 × optimization loops
- Each loop: 100-500 function evaluations
- Total: **2,500-25,000 optimization iterations during Ouroboros run**
- **CPU choke: Tokio reactor stalls waiting for Ouroboros to finish**
- If Ouroboros takes 30 minutes instead of 10, market opens with stale calibration

### The Fix

**Strategy: SEPARATE FITTING FROM INFERENCE**

1. **Fit once per day (nightly Ouroboros step):**
   ```python
   # ouroboros/step_1_garch_calibration.py (new step, NEW FILE)
   def calibrate_garch_daily(tickers: List[str], lookback_days: int = 252) -> Dict[str, GARCHParams]:
       """Run GARCH(1,1) MLE fitting ONCE per day (cached)."""
       calibration = {}
       for ticker in tickers:
           returns = get_daily_returns(ticker, lookback_days)
           garch = GARCH11Model(returns=returns)

           # Single optimization (not per-tick!)
           params = garch.fit(method='L-BFGS-B', maxiter=500)

           # Cache to disk
           calibration[ticker] = {
               'omega': params.omega,
               'alpha': params.alpha,
               'beta': params.beta,
               'sigma2_init': params.sigma2_init,
           }

       write_json('calibration/garch_params.json', calibration)
       return calibration

   # Call ONCE per day at 23:50 UTC
   # Takes: ~1 minute for 50 assets (optimizable to 30s with parallel)
   ```

2. **Use parameters in real-time (O(1) Rust inference):**
   ```rust
   // rust_core/src/garch_inference.rs (new module, NEW FILE)
   pub struct GARCHInference {
       omega: f64,
       alpha: f64,
       beta: f64,
       sigma2_prev: f64,  // Persisted state
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

3. **Architecture:**
   ```
   Ouroboros (nightly, 23:50 UTC):
     Step 0 (NEW): Fit GARCH(1,1) MLE → save to garch_params.json
     Step 1-9: Use cached params, call GARCH::update_residual() [O(1)]

   Rust Engine (live):
     Load garch_params.json at boot
     On every tick: residual = garch.update_residual(tick_return)
     Pass residual to EVT tail estimator
   ```

### Implementation Checklist

- [ ] Create `ouroboros/step_0_garch_calibration.py` (new file, ~150 lines)
- [ ] Create `rust_core/src/garch_inference.rs` (new module, ~100 lines)
- [ ] Verify `calibration/garch_params.json` schema
- [ ] Add load-and-cache logic to `phase_15_risk_gate.rs`
- [ ] **Acceptance Test (RM-1-AT):** Run 50-asset GARCH fit, verify <2 min elapsed time

### Risk Mitigation

**If garch_params.json is missing on boot:** Use cache from previous day (24-hour stale but stable).

---

## MANDATE 2: TOKIO::FS BLOCKING POOL DEATH SPIRAL (RM-2)

### The Problem

**Current (BROKEN):**
```rust
// In phase_22_wal_writer.rs (current implementation)
pub async fn write_wal_event(&self, event: WalPayload) -> Result<()> {
    let json = serde_json::to_string(&event)?;

    tokio::fs::OpenOptions::new()
        .append(true)
        .open("/app/logs/active_state.wal")
        .await?  // <-- BLOCKING on I/O thread pool
        .write_all(json.as_bytes())
        .await?
}

// Called on EVERY tick (10,000 events/sec during market burst)
// Each write spawns a task that grabs a spawn_blocking slot
// After 512 concurrent I/O tasks → pool exhausted
// All other tasks (RiskGate, HotScanner) block waiting for I/O
```

**Issue:**
- tokio::fs uses `spawn_blocking()` under the hood
- Default pool size: 512 threads
- During 10,000 tick/sec burst: WAL writes queue up instantly
- Once pool is full: **EVERY async task that calls tokio::fs will block indefinitely**
- RiskGate waiting for WAL write → HotScanner waiting for RiskGate → entire system frozen

### The Fix

**Strategy: DEDICATED SYNCHRONOUS WAL THREAD + LOCK-FREE QUEUE**

1. **Create WAL actor thread (runs on dedicated OS thread):**
   ```rust
   // rust_core/src/wal_actor.rs (NEW FILE, ~250 lines)
   use crossbeam::channel::{unbounded, Receiver};
   use std::fs::OpenOptions;
   use std::io::Write;
   use std::thread;

   pub enum WalCommand {
       WriteEvent(Vec<u8>),  // Pre-serialized JSON bytes
       Flush,
       Shutdown,
   }

   pub struct WalActor {
       rx: Receiver<WalCommand>,
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
                   WalCommand::WriteEvent(bytes) => {
                       let _ = file.write_all(&bytes);
                       batch_count += 1;

                       // Batch fsync: every 100 writes
                       if batch_count >= 100 {
                           let _ = file.sync_all();
                           batch_count = 0;
                       }
                   }
                   WalCommand::Flush => {
                       let _ = file.sync_all();
                   }
                   WalCommand::Shutdown => break,
               }
           }
       }
   }
   ```

2. **Async interface (non-blocking enqueue):**
   ```rust
   // In main.rs or engine.rs
   pub struct WalWriter {
       tx: crossbeam::channel::Sender<WalCommand>,
   }

   impl WalWriter {
       pub fn write_event_async(&self, event: &WalPayload) -> Result<()> {
           let json = serde_json::to_string(event)?;

           // Non-blocking send to channel
           // If channel is full (pathological case), return error
           self.tx.try_send(WalCommand::WriteEvent(json.into_bytes()))
               .map_err(|_| EngineError::WalChannelFull)
       }

       pub fn flush(&self) -> Result<()> {
           self.tx.try_send(WalCommand::Flush)
               .map_err(|_| EngineError::WalChannelFull)
       }
   }
   ```

3. **Spawn WAL actor on boot:**
   ```rust
   // In main.rs
   let (wal_tx, wal_rx) = crossbeam::channel::unbounded();

   std::thread::Builder::new()
       .name("wal-actor".to_string())
       .spawn(move || {
           let wal_actor = WalActor {
               rx: wal_rx,
               file_path: "/app/logs/active_state.wal".to_string(),
           };
           wal_actor.run();
       })?;

   let wal_writer = WalWriter { tx: wal_tx };
   ```

4. **Architecture:**
   ```
   Tokio Runtime (Async):
     RiskGate → wal_writer.write_event_async(event)
       └─> wal_tx.try_send(WalCommand) [non-blocking, instant]

   OS Dedicated Thread (Sync):
     while event = wal_rx.recv() {
         file.write_all(event)
         if batch_count % 100 == 0: file.sync_all()
     }
   ```

### Implementation Checklist

- [ ] Create `rust_core/src/wal_actor.rs` (new module, ~250 lines)
- [ ] Update `phase_22_wal.rs` to use WalWriter with unbounded channel
- [ ] Verify `try_send` error handling (log + continue, never panic)
- [ ] Set WAL actor thread name + thread affinity (optional: pin to CPU 0)
- [ ] **Acceptance Test (RM-2-AT):** Inject 10,000 tick/sec burst, verify WAL write latency <1ms (no blocking)

### Risk Mitigation

**If WAL channel overflows (pathological):** Log error + continue trading (WAL writes are not in critical path once event is processed). Next graceful shutdown will retry.

---

## MANDATE 3: PYCALL FFI JSON OVERHEAD (RM-3)

### The Problem

**Current (BROKEN):**
```rust
// In python_bridge.rs (hypothetical current implementation)
pub fn call_python_analysis(data: TickContext) -> Result<AnalysisResult> {
    // Convert Rust struct → JSON string
    let json_str = serde_json::to_string(&data)?;

    Python::with_gil(|py| {
        let py_json = PyString::new(py, &json_str);

        // Python deserializes JSON → dict
        let py_dict: PyDict = py.eval(
            &format!("json.loads('{}')", py_json),
            None,
            None
        )?;

        // Call Python function
        ouroboros_module.call_method1(py, "analyze", (py_dict,))?
    })
}
```

**Issues:**
- 2 serialization passes: Rust struct → JSON, JSON → Python dict
- 2 deserialization passes on return
- JSON parsing in Python: O(N) character scanning
- Latency: ~5-10ms per call (unacceptable for HotScanner ticks)

### The Fix

**Strategy: USE NATIVE PyO3 CONVERSION TRAITS**

1. **Define Rust struct with #[pyclass] macro:**
   ```rust
   // rust_core/src/types/tick_context.rs
   use pyo3::prelude::*;

   #[pyclass]
   pub struct TickContext {
       #[pyo3(get, set)]
       pub ticker_id: u32,

       #[pyo3(get, set)]
       pub price: f64,

       #[pyo3(get, set)]
       pub bid_size: u64,

       #[pyo3(get, set)]
       pub ask_size: u64,
   }

   #[pymethods]
   impl TickContext {
       #[new]
       pub fn new(ticker_id: u32, price: f64, bid_size: u64, ask_size: u64) -> Self {
           Self { ticker_id, price, bid_size, ask_size }
       }
   }
   ```

2. **Direct Rust → Python conversion (zero-copy):**
   ```rust
   pub fn call_python_analysis(data: TickContext) -> Result<AnalysisResult> {
       Python::with_gil(|py| {
           // Convert Rust struct → Python object directly
           let py_context = data.into_py(py);

           // Call Python function with native object (no JSON!)
           let result = ouroboros_module.call_method1(py, "analyze", (py_context,))?;

           // Convert result back to Rust
           let analysis: AnalysisResult = result.extract(py)?;
           Ok(analysis)
       })
   }
   ```

3. **Python side (simple):**
   ```python
   # ouroboros/analysis.py
   def analyze(tick_context):  # Receives native TickContext object
       return {
           'signal': tick_context.bid_size > tick_context.ask_size,
           'momentum': (tick_context.price - prev_price) / prev_price,
       }
   ```

### Implementation Checklist

- [ ] Add `#[pyclass]` macro to `TickContext` and `AnalysisResult` structs
- [ ] Update `python_bridge.rs` to use `.into_py()` and `.extract()`
- [ ] Remove all `serde_json::to_string` calls in FFI path
- [ ] Verify Cargo.toml has `pyo3 = { version = "0.20", features = ["auto-initialize"] }`
- [ ] **Acceptance Test (RM-3-AT):** Measure FFI round-trip latency: should be <0.5ms (was ~5-10ms with JSON)

### Risk Mitigation

**If Python side breaks:** Add `TryFrom<PyAny>` fallback that re-tries with JSON parsing (slower but safe).

---

## MANDATE 4: HUBER LOSS STATIC DELTA (RM-4)

### The Problem

**Current (BROKEN):**
```rust
// In student_t_kalman.rs (Huber loss robustification)
const HUBER_DELTA: f64 = 1.5;  // HARDCODED!

pub fn huber_loss(residual: f64, delta: f64) -> f64 {
    if residual.abs() <= delta {
        0.5 * residual.powi(2)  // Quadratic (trust this observation)
    } else {
        delta * (residual.abs() - 0.5 * delta)  // Linear (ignore outliers)
    }
}
```

**Issue:**
- Huber $\delta$ controls the "outlier threshold"
- Hardcoded value works for ONE volatility regime
- Market changes regime → $\delta$ is now wrong
- **Example:**
  - Normal regime: $\sigma = 0.5\%$ → outliers start at $\delta = 1.5 \times 0.5\% = 0.75\%$
  - Volatility spike: $\sigma = 5\%$ → same $\delta$ now triggers on every quote (ignoring real data)
  - Or inverse: low-vol period → $\delta$ is too loose, accepting outliers

### The Fix

**Strategy: DYNAMIC DELTA = 1.345 × MEDIAN ABSOLUTE DEVIATION (MAD)**

```rust
// rust_core/src/student_t_kalman.rs (update)
pub struct StudentTKalman {
    residuals_buffer: VecDeque<f64>,  // Last 100 residuals
    huber_delta: f64,
    // ... other fields
}

impl StudentTKalman {
    pub fn update_huber_delta(&mut self) {
        if self.residuals_buffer.len() < 10 {
            return;  // Not enough data
        }

        // Calculate Median Absolute Deviation
        let mut sorted = Vec::from_iter(
            self.residuals_buffer.iter().map(|r| r.abs())
        );
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());

        let median = sorted[sorted.len() / 2];
        let mad = sorted.iter()
            .map(|r| (r - median).abs())
            .collect::<Vec<_>>()
            .sort_by(|a, b| a.partial_cmp(b).unwrap());

        let mad_of_mad = sorted[sorted.len() / 2];  // Reuse sorted for efficiency

        // Huber delta: 1.345 × MAD (magic constant from Huber's paper)
        self.huber_delta = 1.345 * mad_of_mad;
    }

    pub fn measurement_update(&mut self, observation: f64) {
        let residual = observation - self.state_estimate;

        // Adaptive Huber loss using current delta
        let weight = if residual.abs() <= self.huber_delta {
            1.0
        } else {
            self.huber_delta / residual.abs()
        };

        // Apply weighted Kalman update
        self.kalman_gain *= weight;
        // ... rest of update

        self.residuals_buffer.push_back(residual);
        if self.residuals_buffer.len() > 100 {
            self.residuals_buffer.pop_front();
        }

        // Recalculate delta every 50 ticks
        if self.tick_count % 50 == 0 {
            self.update_huber_delta();
        }
    }
}
```

### Implementation Checklist

- [ ] Add `residuals_buffer: VecDeque<f64>` to StudentTKalman struct
- [ ] Implement `update_huber_delta()` method
- [ ] Call `update_huber_delta()` every 50 ticks in measurement update
- [ ] Verify `huber_delta` is being used in `huber_loss()` calculation
- [ ] **Acceptance Test (RM-4-AT):** Inject volatility spike; verify delta adapts within 100 ticks

### Risk Mitigation

**If residuals buffer is empty:** Use default `HUBER_DELTA = 1.5` as fallback.

---

## MANDATE 5: SYS.EXIT(255) FORK BOMB (RM-5)

### The Problem

**Current (BROKEN):**
```rust
// In command_wrapper.rs (Rust subprocess supervisor)
pub async fn respawn_python_subprocess(&mut self) -> Result<()> {
    loop {
        let mut child = tokio::process::Command::new("python")
            .arg("ouroboros.py")
            .spawn()?;

        match child.wait().await {
            Ok(status) if status.code() == Some(255) => {
                log::info!("CleanFlushRequested. Respawning immediately.");
                // INSTANTLY respawn → no backoff
            }
            Ok(status) => {
                log::error!("Process exited: {:?}", status);
                break;
            }
            Err(e) => {
                log::error!("Wait failed: {}", e);
                break;
            }
        }
    }
}
```

**Issue:**
- If Python has a persistent bug that crashes → always exits with 255
- Rust respawns immediately → Python crashes immediately → respawns immediately
- **Fork bomb:** System creates 1,000+ new processes in seconds
- **CPU spike:** Each process fork consumes CPU
- **PID table exhaustion:** Kernel PID limit hit (usually 32,768)
- **System becomes unresponsive** waiting for PIDs to recycle

### The Fix

**Strategy: EXPONENTIAL BACKOFF + SYSTEMHALT AFTER 3 CRASHES IN 60 SEC**

```rust
// rust_core/src/command_wrapper.rs (update)
pub struct PythonSubprocessManager {
    recent_exits: VecDeque<Instant>,  // Last 5 exits with timestamps
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

                        // Trigger emergency halt
                        self.telegram.send(
                            "🚨 CRITICAL: Python subprocess crash loop detected. System halted."
                        ).await;

                        return Err(EngineError::SystemHaltRequested);
                    }

                    // Exponential backoff: 1s, 2s, 4s, 8s
                    let backoff = std::cmp::min(
                        self.respawn_backoff_ms,
                        60_000  // Cap at 60 seconds
                    );

                    log::warn!("Python exited (255). Respawning in {}ms.", backoff);
                    tokio::time::sleep(Duration::from_millis(backoff)).await;

                    // Increase backoff for next retry
                    self.respawn_backoff_ms = (self.respawn_backoff_ms * 2).min(60_000);
                }
                Ok(status) => {
                    // Non-255 exit = fatal error, don't respawn
                    log::error!("Python exited fatally: {:?}", status);
                    self.respawn_backoff_ms = 1_000;  // Reset backoff
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

### Implementation Checklist

- [ ] Add `recent_exits: VecDeque<Instant>` + `respawn_backoff_ms: u64` to CommandWrapper
- [ ] Implement `record_exit()` and `count_recent_exits()`
- [ ] Implement exponential backoff logic (1s → 2s → 4s → 8s → 60s cap)
- [ ] On 3+ crashes in 60s: send Telegram alert + return SystemHalt error
- [ ] In main.rs: catch `SystemHalt` and enter HALT regime
- [ ] **Acceptance Test (RM-5-AT):** Force Python to exit(255) 5 times; verify (1) backoff increases, (2) SystemHalt triggered, (3) Telegram alert sent

### Risk Mitigation

**If SystemHalt is triggered:** Manual operator intervention required (bot message will prompt). User sends `/RESUME` to retry (resets backoff counter).

---

## WEEK 1 EXECUTION PLAN

### Monday (2h)
- [ ] RM-1: Create `ouroboros/step_0_garch_calibration.py` + `rust_core/src/garch_inference.rs`
- [ ] Verify GARCH fit time <2 min for 50 assets

### Tuesday (2h)
- [ ] RM-2: Create `rust_core/src/wal_actor.rs` + update WalWriter interface
- [ ] Verify WAL write latency <1ms under 10k tick/sec burst

### Wednesday (2h)
- [ ] RM-3: Add `#[pyclass]` to TickContext + AnalysisResult
- [ ] Update `python_bridge.rs` to use native conversions
- [ ] Verify FFI round-trip latency <0.5ms

### Wednesday (1h)
- [ ] RM-4: Implement dynamic Huber delta with MAD calculation
- [ ] Verify delta adapts within 100 ticks on volatility spike

### Thursday (0.5h)
- [ ] RM-5: Implement respawn backoff + crash counter
- [ ] Verify fork bomb prevention (max 3 respawns in 60s)

### Thursday (0.5h)
- [ ] **Integration Testing:** Run all 5 acceptance tests in sequence
- [ ] Code review + merge to main branch

---

## ACCEPTANCE TEST SUITE

```bash
# Run after refactoring sprint
cargo test rm_1_at -- --nocapture  # GARCH fit <2 min
cargo test rm_2_at -- --nocapture  # WAL latency <1ms
cargo test rm_3_at -- --nocapture  # FFI latency <0.5ms
cargo test rm_4_at -- --nocapture  # Huber delta adapts
cargo test rm_5_at -- --nocapture  # Respawn backoff prevents fork bomb

# All 5 must pass before Phase 8 kickoff
```

---

## GATE CRITERIA (PHASE 8 READINESS)

**PASS ONLY IF:**
- ✅ All 5 mandates implemented + all ATs green
- ✅ `cargo build` succeeds (no warnings in RM-related files)
- ✅ `cargo fmt` passes
- ✅ `cargo clippy` passes
- ✅ No blocking std::fs calls in async path (grep `-r "std::fs" rust_core/src/ | grep -v "wal_actor"`)
- ✅ No direct `#[tokio::main]` or `spawn_blocking` on I/O (grep `-r "spawn_blocking" rust_core/src/`)

---

## RISK MITIGATION SUMMARY

| Mandate | Disaster Scenario | Mitigation |
|---------|------------------|-----------|
| **RM-1** | GARCH fit freezes Tokio | Use cached params from previous day |
| **RM-2** | WAL blocking pool exhausted | Dedicated sync thread + crossbeam channel |
| **RM-3** | FFI JSON serialization overhead | Native PyO3 conversions (zero-copy) |
| **RM-4** | Huber delta wrong in new regime | Dynamic MAD-based calculation |
| **RM-5** | sys.exit(255) fork bomb | Exponential backoff + 3-crash halt |

---

*AEGIS_WEEK1_REFACTORING_SPRINT.md — Generated 2026-03-10*
*Status: BLOCKING PHASE 8 — MUST COMPLETE BEFORE KICKOFF*
*ETA: 7.5 hours (Mon-Thu)*
