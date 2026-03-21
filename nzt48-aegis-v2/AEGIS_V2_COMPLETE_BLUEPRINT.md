# AEGIS V2 COMPLETE BLUEPRINT
## Every Single Detail of the 15-Week Execution Plan

**Status**: LOCKED FOR EXECUTION (2026-03-10)
**Timeline**: 15 weeks to live capital deployment (Late June 2026)
**Architecture**: IBKR-Primary + yfinance Fallback (Option D+)
**Total Effort**: ~504 hours code + testing
**Total Cost (Dev)**: $0 | **Total Cost (Live)**: ~$65/month

---

## TABLE OF CONTENTS

1. [Executive Summary](#executive-summary)
2. [Phase 0: Bootstrap (~87 minutes)](#phase-0-bootstrap)
3. [Phase 1: Week 1 Refactoring (7.3 hours)](#phase-1-refactoring)
4. [Phase 2: Phase 8 Infrastructure (77.4 hours)](#phase-2-infrastructure)
5. [Phase 3: Phases 11-23 Sequential Build (358 hours)](#phase-3-sequential)
6. [Phase 4: Crucible Validation (63 hours)](#phase-4-crucible)
7. [Phase 5: ⏸️ PAUSED (Ready but not deployed)](#phase-5-paused)
8. [Data Architecture (IBKR-Primary)](#data-architecture)
9. [Security Protocols (Ralph Wiggum, Anchor, Checkpoint, H-07)](#security-protocols)
10. [Complete File Structure](#file-structure)

---

# EXECUTIVE SUMMARY

## The Decision: Option D+ (IBKR-Primary Zero-Cost Architecture)

### Key Metrics

| Metric | Value |
|--------|-------|
| **Primary Data Source** | IBKR Gateway (real-time, already connected for execution) |
| **Fallback Data Source** | yfinance (free, graceful degradation) |
| **Corporate Actions** | Polygon Starter (dividends/splits only, 0-6 calls/night) |
| **Data Vendor Cost** | $0/month |
| **Data Latency** | <100ms (IBKR) vs. 2-5s (yfinance) |
| **Bootstrap Timeline** | 87 minutes (11 min faster than yfinance-only) |
| **Daily Ouroboros Time** | <30 min (vs. 21.7h without caching) |
| **Nightly API Calls** | 0-1 (IBKR native) + 1-6 (Polygon fallback) = 1-6 max |
| **Real-Time Quotes** | ✅ YES (IBKR Level 1 bid/ask/spread) |
| **H-07 Auto-Reconnection** | ✅ YES (10-min timeout, Docker restart) |
| **Scaling Ceiling** | £50k AUM comfortable; upgrade to Option A/B at £100k+ |
| **Cost per Month (Live)** | ~$65 (AWS EC2 + EBS post-free-tier) |

---

# PHASE 0: BOOTSTRAP (~87 minutes)

## Overview

Phase 0 is **fully automated**. No user interaction needed (except approval gate before start).

### Timeline

```
09:00 UTC — Start
09:00-09:38 — Dividend calendar (Polygon, 150 calls, 37.5 min)
09:38-10:15 — Splits calendar (Polygon, 150 calls, 37.5 min)
10:15-10:17 — IBKR LSE contract discovery (real-time, 2 min)
10:17-10:25 — GARCH fitting + adjustment (8 min)
10:25-10:27 — Validation (2 min)
10:27 UTC — Complete & ready for Phase 1
```

### Task 1: Dividend Calendar Bootstrap (37.5 minutes)

**Mandatory Fix**: Strict sequential pagination with 15-second delays

**File**: `python_brain/ouroboros/bootstrap_dividend_calendar.py`

**Algorithm**:
1. Start with cursor = None
2. Fetch page (limit=1000, sort=ex_dividend_date, order=desc)
3. Wait 15 seconds (1 call/4-min rate limit = 15 sec per call)
4. Continue pagination until no more results
5. Save to `data/dividend_calendar.json` (5,200+ tickers × 5+ years)

**Critical Details**:
- Sequential only (no async, no ThreadPoolExecutor — will trigger 429 ban)
- 15-second delays between calls (not 1-2 seconds)
- Expect ~150 API calls total
- Total time: 37.5 minutes (150 calls × 15 sec)
- Checkpoint after every 10 calls (resume from last checkpoint if network failure)

**Expected Output**:
```json
{
  "AAPL": [
    {"ex_date": "2025-11-07", "record_date": "2025-11-10", "pay_date": "2025-11-20", "amount": 0.25},
    ...5000+ more dividends...
  ],
  ...5200+ tickers...
}
```

### Task 2: Splits Calendar Bootstrap (37.5 minutes)

**File**: `python_brain/ouroboros/bootstrap_splits_calendar.py`

**Same algorithm as dividends**:
1. Fetch splits via `/v3/reference/splits` endpoint
2. Sequential pagination with 15-second delays
3. 150 API calls expected
4. Save to `data/splits_calendar.json`

**Critical Details**:
- Prevents 1000% Kalman filter spikes from stock splits
- Adjusts historical prices before GARCH fitting
- Cached locally to avoid repeated API calls

**Expected Output**:
```json
{
  "AAPL": [
    {"ex_date": "2020-08-31", "split_from": 1, "split_to": 4},
    ...history...
  ],
  ...tickers with splits...
}
```

### Task 3: IBKR LSE Contract Discovery (2 minutes)

**File**: `python_brain/ouroboros/ibkr_bootstrap.py` (copied from V1 IBKRSource)

**12 LSE Leveraged ETPs to discover**:
```
QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L,
TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L
```

**IBKR Primary Path** (if IBKR available):
```python
ibkr = IBKRSource()
for ticker in lse_tickers:
    contract = ibkr._get_contract(ticker)         # Contract qualification
    bars = ibkr.fetch_bars(ticker, period='60d') # 60-day history
    quote = ibkr.fetch_quote(ticker)              # Real-time Level 1
    # Output: {ticker, bid, ask, spread_bps}
```

**yfinance Fallback** (if IBKR unavailable >10 min):
```python
import yfinance as yf
for ticker in lse_tickers:
    data = yf.download(ticker, period='60d', progress=False)
    # Output: OHLCV data
```

**Expected Output**:
- 12 LSE contracts with real-time bid/ask quotes (if IBKR available)
- Spread in basis points (bps) for slippage calculation
- 60-day historical bars

**Fallback Chain**:
1. IBKR Gateway (primary) → <100ms latency, real-time quotes
2. yfinance (fallback) → 2-5s latency, guaranteed availability

### Task 4: GARCH Calibration (8 minutes)

**File**: `rust_core/src/garch_inference.rs`

**Assets to fit**:
- 50 US assets (top GARCH-suitable tickers)
- 12 LSE leveraged ETPs (real-time quotes from Task 3)

**GARCH(1,1) Fitting**:
```
sigma²(t) = ω + α × r²(t-1) + β × σ²(t-1)
```

**Using Polygon Grouped endpoint**:
- 1 API call (not 62) — massive optimization
- Returns OHLCV for all symbols simultaneously
- Fit to 60-day history (4 years for long-term stability)

**Output**: `data/garch_params.json`
```json
{
  "AAPL": {"omega": 0.000001, "alpha": 0.05, "beta": 0.94, "sigma2_prev": 0.0001},
  "QQQ3.L": {"omega": 0.000001, "alpha": 0.06, "beta": 0.93, "sigma2_prev": 0.0002},
  ...all 62 assets...
}
```

**Expected Fit Quality**:
- Alpha + Beta < 1.0 (stability condition)
- Typical: Alpha 0.04-0.08, Beta 0.91-0.96
- Sigma2_prev initialized for real-time O(1) calculation

### Task 5: Validation (2 minutes)

**Checks**:
1. ✅ dividend_calendar.json exists + non-empty (5,200+ tickers)
2. ✅ splits_calendar.json exists + non-empty
3. ✅ IBKR LSE discovery: 12 contracts with valid quotes
4. ✅ garch_params.json: All 62 assets fitted (alpha+beta < 1.0)
5. ✅ All outputs saved to `data/` directory

**Exit Gate**: `[c]ontinue to Phase 1` or `[q]uit`

---

# PHASE 1: REFACTORING (7.3 hours)

## Overview

Phase 1 consists of **5 isolated coding sessions** (RM-1 through RM-5) + **Friday paper validation**.

Each session:
- Has detailed specification (see AEGIS_CODEX.md PART 3)
- Pauses for approval gate before/after
- Runs `cargo test` gate to confirm passing
- Updates CORE_TYPES_ANCHOR.md after completion

### RM-1: GARCH Daily Fit + Real-Time Residuals (2.5 hours)

**Files**:
- `python_brain/ouroboros/step_0_garch_calibration.py` (Python)
- `rust_core/src/garch_inference.rs` (Rust)

**What RM-1 does**:
- Takes real-time price ticks (from IBKR or yfinance)
- Uses cached sigma2_prev from Phase 0
- Calculates residual in O(1) time
- Updates sigma2_prev for next tick
- Persists to Write-Ahead Log (WAL) every tick

**Test Gate**:
```bash
cargo test test_garch_inference --lib ✓
```

**Approval Gate**: Before `APPROVED RM-1`

---

### RM-2: WAL Dedicated Thread (3 hours)

**File**: `rust_core/src/wal_engine.rs`

**What RM-2 does**:
- Creates dedicated std::thread (not tokio) for WAL writes
- Bounded channel (10,000 message capacity)
- Graceful drop on full queue (no panic)
- Expected latency: <1ms per write

**Test Gate**:
```bash
cargo test test_wal_bounded_channel_latency --lib ✓
```

**Expected**: No OOM under 10k ticks/sec load

**Approval Gate**: Before `APPROVED RM-2`

---

### RM-3: PyO3 Native FFI (1 hour)

**File**: `rust_core/src/pyo3_bridge.rs`

**What RM-3 does**:
- Zero-copy conversions between Python + Rust
- No JSON serialization overhead
- TickContext extraction (bid, ask, spread, timestamp)
- GIL-safe async handling

**Expected Latency**: <0.5ms (was 5-10ms with JSON)

**Test Gate**:
```bash
cargo test test_pyo3_tick_extraction_latency --lib ✓
```

**Approval Gate**: Before `APPROVED RM-3`

---

### RM-4: Dynamic Huber Delta (0.5 hours)

**File**: `rust_core/src/kalman_huber_delta.rs`

**What RM-4 does**:
- Calculates Huber delta dynamically from MAD (median absolute deviation)
- Volatility regime adaptation
- Formula: `Delta = 1.345 × MAD`
- Prevents divide-by-zero on pegged prices

**Test Gate**:
```bash
cargo test test_kalman_huber_regime_change --lib ✓
```

**Approval Gate**: Before `APPROVED RM-4`

---

### RM-5: Exponential Backoff (0.5 hours)

**File**: `rust_core/src/exponential_backoff.rs`

**What RM-5 does**:
- Fork-bomb prevention
- Backoff sequence: 1s → 2s → 4s → 8s → 60s cap
- Regime transitions: normal → YELLOW (50% reduce) → RED (halt)
- Applied to subprocess retries, API call retries

**Test Gate**:
```bash
cargo test test_subprocess_fork_bomb_prevention --lib ✓
```

**Approval Gate**: Before `APPROVED RM-5`

---

### Friday: 24-Hour Paper Validation

**What happens**:
- Zero container restarts (H-07 auto-reconnection not triggered)
- All risk gates functional
- WAL writes complete
- PyO3 lifetime correct (no segfaults)

**Approval Gate**: Before `APPROVED PHASE 1`

---

# PHASE 2: INFRASTRUCTURE SEAL (77.4 hours)

## Overview

Phase 2 = Phase 8 in original AEGIS plan. Builds complete infrastructure.

### Deliverables

**20 Standard Components (SC-01 through SC-20)**

See AEGIS_CODEX.md PART 4 for complete specifications. Examples:
- SC-01: Data feed prioritization
- SC-02: Order router
- SC-03: Risk gate manager
- ...up to SC-20

**6 Wiring Patches (WP-1 through WP-6)**

Embedded within SC items to integrate components.

**26 Acceptance Tests (AT-1 through AT-26)**

All must pass before proceeding.

**48-hour Continuous Paper Run**

Zero crashes, all systems stable.

---

# PHASE 3: PHASES 11-23 SEQUENTIAL BUILD (358 hours)

## Overview

Builds the complete trading engine sequentially.

### Phase 11-12: Stress Testing + EGARCH (83.5 hours)

- Stress test all components under extreme conditions
- EGARCH(1,1,1) fitting (asymmetric volatility)
- Leverage modeling

### Phase 13: Dynamic Kelly Sizing (30 hours)

- Kelly criterion for position sizing
- Dynamic adjustment based on win rate + Sharpe

### Phase 14: VWAP Smart Routing (25 hours)

- Volume-weighted average price routing
- Minimize slippage

### Phase 15: LSTM/GRU Attention Networks (80 hours)

- Deep learning for price prediction
- Attention mechanism for feature importance

### Phases 16-20: Signals + Risk Gates (195 hours)

- Multiple signal generators
- Risk gate implementation (spread, volatility, correlation)
- Circuit breakers

### Phase 21: DCC-GARCH Correlations (70 hours)

- Dynamic conditional correlations
- Cross-asset risk monitoring

### Phase 22: Emergency Modes (35 hours)

- Market crisis protocols
- Automatic de-risking
- Manual override capabilities

---

# PHASE 4: CRUCIBLE VALIDATION (63 hours)

## Overview

Execute 100 paper trades and validate against strict criteria.

### Validation Gates

**Pass Criteria** (all must be met):
- ✅ Win rate ≥ 40% (statistically significant)
- ✅ Sharpe ratio ≥ 0.8 (world-class)
- ✅ Max drawdown ≤ 2.5% (hard stop)
- ✅ Trade distribution ≥ 4 uncorrelated sectors

### Walk-Forward Validation

- 10 × 70-trade windows
- Each window: 50-trade training, 20-trade test
- Ensures generalization (not overfitting to phase 0-3)

### System Fully Validated

Upon completion: **System ready for live capital deployment**

---

# PHASE 5: ⏸️ PAUSED

## Status

System is **fully developed, tested, and validated**.

**NOT deployed to live capital** by default.

**Waiting for explicit authorization** to deploy.

### How to Deploy When Ready

```bash
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/scripts/deploy_live_capital.sh
```

This will:
1. Verify all Phase 0-4 gates passed
2. Switch from paper trading to live capital
3. Deploy to EC2 production instance
4. Enable Telegram alerts for live execution

---

# DATA ARCHITECTURE

## Primary: IBKR Gateway

**Connection**: Direct to Interactive Brokers via ib_insync

**Features**:
- Real-time Level 1 quotes (bid/ask/last/spread)
- Historical bars (1m, 5m, 15m, 30m, 1h, 1d)
- Order execution (live capital phase)
- Account information
- Contract qualification

**Latency**: <100ms

**Cost**: $0 (already connected for execution)

**H-07 Auto-Reconnection Protocol**:
- 10-minute timeout before fallback
- Docker restart on 3 consecutive failures
- Telegram alerts on disconnect/reconnect
- Automatic resume on reconnection

**File**: `/Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py` (565 lines, production-ready)

---

## Fallback: yfinance

**Connection**: Web scraper (free, no API key)

**Features**:
- Historical OHLCV data (unlimited)
- Real-time quotes (not Level 1, but last price)
- LSE ticker support (QQQ3.L, TSL3.L, etc.)
- No rate limits

**Latency**: 2-5 seconds

**Cost**: $0

**Graceful Degradation**:
- Automatic fallback if IBKR unavailable >10 min
- No user intervention needed
- System continues operating
- Performance degrades but remains functional

---

## Auxiliary: Polygon Starter

**Connection**: REST API (free tier)

**Features**:
- Dividend calendar (5,200+ tickers, 5+ years history)
- Stock splits calendar
- Corporate actions

**Usage**:
- Phase 0 bootstrap only (1 time)
- Nightly ex-date validation (0-1 call/night after bootstrap)
- NO real-time quotes

**Latency**: 0.5-2 seconds (not critical, cached locally)

**Cost**: $0 (Polygon Starter free tier)

**API Key**: `[REDACTED - see .env]`

---

## Data Feed Chain Diagram

```
AEGIS V2 System
  ├─ Real-Time Ticks (5-second bars)
  │   ├─ PRIMARY: IBKR Gateway (<100ms, Level 1 quotes)
  │   │   ├─ bid/ask/last/spread
  │   │   ├─ timestamp (microsecond precision)
  │   │   └─ contract info
  │   └─ FALLBACK: yfinance (2-5s, last price only)
  │
  ├─ GARCH Calibration (Phase 0)
  │   ├─ Dividend Calendar (Polygon, 150 calls, 37.5 min)
  │   ├─ Splits Calendar (Polygon, 150 calls, 37.5 min)
  │   ├─ Historical bars (IBKR or yfinance)
  │   └─ Output: GARCH parameters (cached)
  │
  └─ Daily Bootstrap (Nightly)
      └─ Validate dividends (Polygon, 0-1 call/night)
      └─ Update splits (cached)
      └─ Ouroboros calibration (<30 min with caches)
```

---

# SECURITY PROTOCOLS

## 1. Ralph Wiggum Protocol (Loop Prevention)

**Purpose**: Prevent infinite loops that crash system

**Implementation**:
- All loops have max iteration cap of 20
- Applied to:
  - cargo build retries
  - test retries
  - API pagination
  - Network reconnection attempts

**Behavior**:
- If loop iteration reaches 20: STOP and ask for help
- Never auto-retry beyond 20
- Log every 5 iterations

**Example**:
```rust
let mut attempt = 0;
loop {
    attempt += 1;
    if attempt > 20 {
        eprintln!("ERROR: Max 20 iterations reached, stopping");
        break;
    }
    // ... retry logic ...
}
```

---

## 2. Anchor Rule (LLM Hallucination Prevention)

**Purpose**: Prevent Claude from hallucinating struct definitions

**Implementation**:
- After EVERY coding session, update `docs/CORE_TYPES_ANCHOR.md`
- Contains exact Rust struct definitions
- Contains exact PyO3 bindings
- Contains exact field names and types

**File**: `docs/CORE_TYPES_ANCHOR.md` (updated after RM-1, RM-2, RM-3, RM-4, RM-5)

**Example**:
```markdown
## GarchState (Rust)
```rust
pub struct GarchState {
    pub sigma2: f64,           // Current variance (sigma²)
    pub r_prev: f64,          // Previous residual
    pub updated_at: u64,      // Unix timestamp
}
```

pub struct TickContext {
    pub bid: f64,
    pub ask: f64,
    pub spread_bps: f64,
    pub timestamp: u64,
}
```

## PyO3 Bridge
```python
class TickContext(NamedTuple):
    bid: float
    ask: float
    spread_bps: float
    timestamp: int
```
```

**Benefit**: Next Claude session can read exact definitions instead of guessing

---

## 3. Checkpoint Rule (Network Resilience)

**Purpose**: Never restart from zero on network failure

**Implementation**:
- All API operations save state to `checkpoint.json`
- Checkpoint after every 10 API calls
- Resume from last checkpoint on restart

**Example Checkpoints**:
- After fetching page 1 of dividend calendar (10 calls)
- After fetching page 2 of splits calendar (20 calls)
- After IBKR contract discovery (30 calls)

**File Format**:
```json
{
  "phase": 0,
  "task": 1,
  "progress": {
    "dividends_page": 5,
    "dividends_cursor": "aHR0cHM6Ly9hcGkucG9...",
    "splits_page": 2,
    "splits_cursor": "bW9yZSBkYXRhIQ==",
    "ibkr_contracts_discovered": 8,
    "garch_fitted_assets": 35
  },
  "timestamp": "2026-03-11T10:15:00Z",
  "status": "in_progress"
}
```

---

## 4. H-07 Auto-Reconnection Protocol (IBKR Reliability)

**Purpose**: Keep system running even if IBKR temporarily disconnects

**Implementation**:

1. **Connection Monitoring** (continuous)
   - Ping IBKR every 5 seconds
   - Track connection status

2. **Timeout Threshold** (10 minutes)
   - If no response for 10 minutes: DEGRADED mode
   - Switch to yfinance fallback
   - Log warning to Telegram

3. **Docker Restart** (on 3 consecutive failures)
   - Stop IB Gateway container
   - Restart IB Gateway container
   - Attempt reconnection

4. **Auto-Resume**
   - When IBKR comes back online: resume primary
   - Telegram alert on resumption

**Failure Scenarios Handled**:
- ✅ Network blip (< 10 min): automatic resume
- ✅ IBKR server maintenance: fallback to yfinance + manual resume
- ✅ IB Gateway crash: Docker restart
- ✅ Complete network failure: system continues with yfinance

---

## 5. Approval Gate Protocol (User Control)

**Purpose**: Prevent automated execution without user consent

**Implementation**:

Every phase pauses and asks:
```
Ready to proceed to: PHASE 1 Refactoring
Options:
  [c] Continue to PHASE 1
  [s] Skip to next phase
  [q] Quit execution

Enter choice [c/s/q]:
```

**Behavior**:
- `[c]` → Proceed to phase
- `[s]` → Skip phase (if applicable)
- `[q]` → Stop execution gracefully

**Logging**:
- Every approval logged to execution journal
- Timestamp + user choice recorded
- Reviewable for audit purposes

**Phases with Gates**:
- Before Phase 0 (approval to start bootstrap)
- After Phase 0 (approval to start Phase 1)
- Before/after each RM-1 through RM-5
- Before Phase 2 (approval to build infrastructure)
- Before Phase 3 (approval to build phases 11-23)
- Before Phase 4 (approval to validate)
- Before Phase 5 (approval to deploy)

---

# COMPLETE FILE STRUCTURE

```
/Users/rr/nzt48-signals/nzt48-aegis-v2/
├── THE_MASTER_COMMAND.sh                 ← Main orchestrator (473 lines)
│                                          ← Pre-flight validation
│                                          ← System briefing
│                                          ← Approval gates
│                                          ← Calls AEGIS_INTERACTIVE.sh
│
├── AEGIS_INTERACTIVE.sh                  ← Interactive Phase 0-5 executor (732 lines)
│                                          ← Phase 0 automated bootstrap
│                                          ← Phases 1-4 approval gates
│                                          ← Phase 5 pause
│
├── QUICK_START.md                        ← User-friendly quick reference
├── MASTER_COMMAND_SUMMARY.md             ← Summary of master command
├── AEGIS_V2_COMPLETE_BLUEPRINT.md        ← This file (every detail)
│
├── docs/
│   ├── AEGIS_CODEX.md                   ← Complete phase specifications (locked)
│   ├── AEGIS_V2_TERMINAL_DIRECTIVE.md   ← Formal execution protocol
│   ├── PLAN_UPDATE_20260310.md          ← IBKR-primary architecture change
│   ├── IBKR_DATAFEED_UPGRADE.md         ← IBKR implementation guide
│   ├── AEGIS_V2_CREDENTIALS.md          ← All API keys + data feeds
│   ├── READY_FOR_SESSION_1.md           ← Phase 0 bootstrap updated
│   └── CORE_TYPES_ANCHOR.md             ← Rust/PyO3 struct definitions (updated per session)
│
├── data/                                 ← Output directory for Phase 0
│   ├── dividend_calendar.json           ← 5,200+ tickers (created by Task 1)
│   ├── splits_calendar.json             ← All splits (created by Task 2)
│   ├── ibkr_lse_discovery.json          ← 12 LSE contracts (created by Task 3)
│   ├── garch_params.json                ← 62 assets fitted (created by Task 4)
│   └── checkpoint.json                  ← API state (updated per 10 calls)
│
├── logs/
│   └── execution/
│       ├── AEGIS_MASTER_*.log           ← Main execution log
│       └── AEGIS_INTERACTIVE_*.log      ← Phase logs
│
├── python_brain/
│   └── ouroboros/
│       ├── step_0_garch_calibration.py  ← Phase 0 GARCH fitting
│       ├── ibkr_bootstrap.py            ← Phase 0 IBKR discovery
│       └── ... more files (Phase 1+)
│
├── rust_core/
│   └── src/
│       ├── garch_inference.rs           ← RM-1 real-time GARCH
│       ├── wal_engine.rs                ← RM-2 WAL thread
│       ├── pyo3_bridge.rs               ← RM-3 FFI bridge
│       ├── kalman_huber_delta.rs        ← RM-4 dynamic delta
│       ├── exponential_backoff.rs       ← RM-5 backoff
│       └── ... more (Phase 2+)
│
└── scripts/
    ├── deploy_live_capital.sh           ← Live deployment (Phase 5)
    └── ... more scripts
```

---

# EXPECTED PERFORMANCE

## Phase 0 Bootstrap

- ⏱️ **87 minutes total** (11 min faster than yfinance-only)
- 🎯 **5,200+ tickers** with 5-year dividend history
- 🎯 **All stock splits** catalogued
- 🎯 **12 LSE tickers** with real-time quotes (if IBKR available)
- 🎯 **50 US assets** with GARCH parameters fitted
- ✅ **Zero network restarts**
- ✅ **All validation tests passed**

## Real-Time Trading (Phases 1-4)

- ⏱️ **IBKR data latency**: <100ms
- 💰 **Cost**: $0 (already connected for execution)
- 🎯 **Real-time Level 1 quotes** (bid/ask/spread)
- 🎯 **Dynamic risk gates** based on current spreads
- 🎯 **Kalman filter** with adaptive Huber delta

## Live Capital (Phase 5+, June 2026)

- 🎯 **Win rate**: ≥40% (statistically significant)
- 🎯 **Sharpe ratio**: ≥0.8 (world-class)
- 🎯 **Max drawdown**: ≤2.5% (hard stop)
- 🎯 **Trade distribution**: ≥4 uncorrelated sectors
- 💰 **Monthly cost**: ~$65 (AWS infrastructure only)

---

# HOW TO EXECUTE

## Step 1: Set Polygon API Key

```bash
export POLYGON_API_KEY="[REDACTED - see .env]"
```

## Step 2: Run Master Command

```bash
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/THE_MASTER_COMMAND.sh
```

## Step 3: Respond to Approval Gates

```
PRE-FLIGHT VALIDATION
✓ POLYGON_API_KEY is set
✓ AEGIS_ROOT exists
✓ Python dependencies available
✓ Polygon API reachable
✓ All pre-flight checks passed

PHASE 0: BOOTSTRAP (~87 minutes)
[Details about each task...]

Ready to proceed with Phase 0 Bootstrap? [y/n]: y
```

## Step 4: Watch Bootstrap Execute

- Task 1: Dividend calendar (real-time progress)
- Task 2: Splits calendar (real-time progress)
- Task 3: IBKR LSE discovery or yfinance fallback
- Task 4: GARCH fitting
- Task 5: Validation

## Step 5: Approval for Phase 1

```
Phase 0 COMPLETE ✓

Ready to proceed to: PHASE 1 Refactoring
Options:
  [c] Continue to PHASE 1
  [s] Skip to next phase
  [q] Quit execution

Enter choice [c/s/q]: c
```

## Step 6: Phases 1-4 (Interactive Coding Sessions)

For each RM (RM-1 through RM-5):
- Approval gate shows specifications
- Claude Code session builds actual code
- Tests run: `cargo test`
- Approval requested after completion
- CORE_TYPES_ANCHOR.md updated

---

# SUCCESS CRITERIA

All phases complete when:

✅ **Phase 0**: All 5 tasks automated, data cached locally, 87 min elapsed
✅ **Phase 1**: RM-1 through RM-5 all `cargo test` passing, 24h paper validation zero restarts
✅ **Phase 2**: All 26 acceptance tests passing, 48h paper run zero crashes
✅ **Phase 3**: All phases 11-23 implemented and tested
✅ **Phase 4**: 100 paper trades with WR≥40%, Sharpe≥0.8, DD≤2.5%
✅ **Phase 5**: System PAUSED, ready for live deployment authorization

---

# NOTES

- This document is the **complete specification** for all 15 weeks
- Every phase, every task, every file, every acceptance criterion
- Use as a **reference during execution** — link back when questions arise
- Update CORE_TYPES_ANCHOR.md after every Claude Code session
- All timing assumes continuous work (not calendar weeks)

---

*AEGIS_V2_COMPLETE_BLUEPRINT.md — Generated 2026-03-10*
*Status: COMPLETE SPECIFICATION, READY FOR EXECUTION*
*Total Lines: ~500 (this document)*
*Total Planning Lines: ~2,500+ (across all documents)*
