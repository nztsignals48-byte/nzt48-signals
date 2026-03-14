# AEGIS — Hardening, Audits & Complexity Reduction
## Sections 2E, 2F, 2G, 2H, 2I from AEGIS Master Plan v16.2
> Source: AEGIS_MASTER_PLAN_v15_MERGED.md | Auto-synced by aegis/sync.sh

### Section 2E: THE CRUCIBLE — Async Hardening & Synthetic Proving Ground {#section-2e}

**CONTEXT**: v15.4 injected massive async complexity (Disruptor Engine, Ghost-Maker, Tachyon, Lead-Lag, Exhaustion Monitor). Standard backtesting on yfinance daily bars CANNOT validate: (a) limit-order queue priority physics, (b) GIL-induced event-loop freezes during Pandas computation, (c) Brain/Muscle state desynchronization when Redis drops messages, (d) parameter curve-fitting risk for Tachyon SG window and Hawkes decay rate. This section specifies 4 hardening modules and 5 chaos drills that transition the system from "Theoretical Apex Predator" to "Bulletproof Institutional Metal."

**THE 4 CATASTROPHIC ASYNC FAILURE MODES**:

| # | Failure Mode | Root Cause | Consequence | Module |
|---|-------------|-----------|-------------|--------|
| F-01 | **GIL Freeze** | Python GIL blocks event loop during Pandas/NumPy ops (100-300ms) | Muscle cannot monitor stops; flash crash during indicator computation = unprotected 3x/5x exposure | **AsyncioHeartbeat** |
| F-02 | **Dark State** | Redis drops message mid-trade; Brain thinks "Flat" while broker says "Long £10k 3x Nasdaq" | Orphan positions accumulate; system enters new trades against existing hidden exposure | **ReconciliationAuditor** |
| F-03 | **Untestable Execution** | yfinance returns OHLCV bars, not order books; Ghost-Maker's queue-priority pegging is pure theory without synthetic matching | Ghost-Maker appears to work in backtests but fails in production due to adverse selection physics | **SyntheticBroker** |
| F-04 | **Parameter Curve-Fitting** | Tachyon SG window (7 bars) and Hawkes decay (beta) are hand-selected; no walk-forward validation | Parameters overfit to training period; edge evaporates in production | **MicrostructureCalibrator** |

#### 2E.1: SyntheticBroker — "The Holodeck" (CR-01) {#section-2e1}

**Priority**: P0 | **Est. Hours**: 12 | **File**: `testing/synthetic_broker.py`

**Purpose**: Local matching engine that simulates LSE ETP microstructure with queue priority and adverse selection physics. Required for validating Ghost-Maker before live deployment.

**Specification**:
- **Order Book Simulation**: FIFO priority queue for limit orders at each price level. Market orders cross the book immediately. Limit orders enter the queue behind existing resting orders.
- **Queue Position Model**: New limit order starts at position N (back of queue). Position advances as orders ahead are filled or cancelled. Fill probability at time t: P(fill|t) = f(queue_pos, trade_rate, price_distance).
- **Adverse Selection Engine (Glosten & Milgrom 1985)**: When a limit order fills, there is a configurable probability (default 35%) that the fill is "adversely selected" — meaning the true price has already moved through the limit price. This models informed flow that picks off stale quotes.
- **Spread Dynamics**: Bid-ask spread follows a mean-reverting process calibrated to LSE leveraged ETP empirical spreads (15-40 bps for 3x ETPs, 25-60 bps for 5x ETPs). Spread widens on high volatility and narrows during liquid periods.
- **Partial Fill Model**: Orders > £2,000 notional may partially fill based on simulated liquidity depth. Depth follows a power-law distribution calibrated to LSE ETP ADV.
- **Integration**: Ghost-Maker connects to SyntheticBroker via same interface as real broker API, enabling drop-in testing.

**Key Design Decision**: The SyntheticBroker does NOT attempt to perfectly replicate LSE. It models the THREE mechanisms that kill retail limit orders: (1) queue priority (you're never first), (2) adverse selection (you fill when the market reverses), (3) partial fills (thin ETP books). If Ghost-Maker survives these three, it survives production.

**Validation Criteria**: Ghost-Maker must achieve >50% passive fill rate (vs maker side) with <15 bps average adverse excursion post-fill across 10,000 simulated trades.

#### 2E.2: AsyncioHeartbeat — "The GIL Monitor" (CR-02) {#section-2e2}

**Priority**: P0 | **Est. Hours**: 6 | **File**: `core/asyncio_heartbeat.py`

**Purpose**: Continuous event-loop lag measurement. If the asyncio event loop is blocked for >50ms (indicating GIL contention from Pandas/NumPy), trip a circuit breaker that halts Brain signals until the Muscle confirms all stops are active.

**Specification**:
- **Heartbeat Mechanism**: Schedule an asyncio callback every 10ms. Measure actual elapsed time vs expected. If actual > expected + 50ms, the event loop was blocked.
- **GIL Freeze Detection**: When lag > 50ms is detected:
  1. Immediately set `brain_circuit_breaker = True` (Brain stops emitting commands)
  2. Log the freeze duration, stack trace of blocking code (via `faulthandler`)
  3. Muscle continues autonomous stop monitoring on cached state
  4. Brain resumes ONLY after Muscle confirms all positions have active stops
- **Statistics**: Track p50, p95, p99 event loop latency. Alert if p95 > 20ms (pre-freeze warning).
- **Integration with Disruptor Engine**: Heartbeat runs inside the Muscle coroutine (the time-critical path). If the Brain's Pandas computation blocks the event loop, the heartbeat detects it and the Muscle's stop-monitoring loop is protected.

**Why 50ms?**: A 3x leveraged ETP can move 0.3% in 50ms during a momentum event (0.1% underlying * 3x * momentum factor). With a 1-ATR stop (typically 0.6-1.2%), 50ms of unmonitored exposure consumes 25-50% of the stop buffer. Anything beyond 50ms makes stops unreliable.

**Critical Insight**: The Disruptor Engine (v15.4) separates Brain and Muscle into cooperative coroutines, but they STILL share the same event loop and the same GIL. The Disruptor prevents logical interference; the Heartbeat prevents PHYSICAL interference from the GIL.

#### 2E.3: ReconciliationAuditor — "The Dark State Eliminator" (CR-03) {#section-2e3}

**Priority**: P0 | **Est. Hours**: 8 | **File**: `core/reconciliation_auditor.py`

**Purpose**: Every 5 minutes, compare the broker API's ground truth (positions, balances, pending orders) against the local SQLite database and Redis state. If ANY mismatch is found, execute hard fail-closed: SIGKILL the engine process and issue Market-On-Close orders for all positions.

**Specification**:
- **Three-Way Reconciliation**: Compare broker API state vs Redis state vs SQLite state. All three must agree on: (a) position count, (b) position direction per ticker, (c) position size per ticker, (d) pending order count, (e) account equity within 0.5% tolerance.
- **Mismatch Classification**:
  - **PHANTOM**: Local state shows position, broker shows flat. Cause: fill message dropped.
  - **ORPHAN**: Broker shows position, local state shows flat. Cause: entry confirmed but ack lost.
  - **SIZE_MISMATCH**: Both show position but different sizes. Cause: partial fill not processed.
  - **DIRECTION_MISMATCH**: Both show position but different direction. Critical corruption.
- **Fail-Closed Protocol**: On ANY mismatch:
  1. Log mismatch type, local state, broker state, Redis state
  2. Send emergency alert (email + Telegram)
  3. **WRITE kill switch to Redis BEFORE SIGKILL (Gemini Q8)**: `redis_client.set("nzt:kill_switch", "TRUE")` with no TTL (persistent). This MUST happen BEFORE step 4 — if SIGKILL is sent first, the kill_switch write never executes and the engine restarts into an infinite crash loop (mismatch still exists -> SIGKILL -> restart -> mismatch -> SIGKILL). The Redis write is the FIRST durable action, not the last.
  4. Issue Market-On-Close orders for ALL positions via broker API (NOT via local engine)
  5. SIGKILL the engine process (not graceful shutdown — corrupted state cannot be trusted)
- **Restart Guard (Gemini Q8)**: On engine startup, `InvariantEnforcer` (RI-02) must check `redis_client.get("nzt:kill_switch")`. If value is `"TRUE"`, the engine MUST NOT start trading. Log `KILL_SWITCH_ACTIVE — manual clearance required`. Block ALL trading until operator manually runs `redis-cli DEL nzt:kill_switch` (or equivalent admin endpoint). This prevents the infinite crash loop where SIGKILL -> restart -> detect mismatch -> SIGKILL.
- **Recovery**: Manual operator intervention required. Operator must: (a) inspect mismatch logs, (b) manually reconcile broker vs local state, (c) clear kill switch (`redis-cli DEL nzt:kill_switch`), (d) restart engine.

**Why 5 Minutes?**: Balances broker API rate limits (most ISA brokers allow 1-6 req/min) against maximum undetected exposure time. In 5 minutes at maximum position size (10% of equity = £1,000), a 3x leveraged ETP can move ~1.5% underlying = ~4.5% ETP = ~£45 unhedged P&L. Acceptable for paper phase; tighten to 60s for live.

**Why SIGKILL?**: Graceful shutdown allows the corrupted state engine to execute "cleanup" trades that may compound the mismatch. A hard kill followed by broker-direct MOC orders ensures the broker (ground truth) handles the position closure, not the compromised local state.

#### 2E.4: MicrostructureCalibrator — "The Anti-Curve-Fitter" (CR-04) {#section-2e4}

**Priority**: P0 | **Est. Hours**: 10 | **File**: `core/microstructure_calibrator.py`

**Purpose**: Walk-forward optimization of Tachyon's Savitzky-Golay window and Hawkes process decay rate using Information Coefficient (Spearman rank correlation between predictor and forward return), NOT in-sample PnL maximization.

**Specification**:
- **Walk-Forward Protocol**:
  - Training window: 20 trading days (rolling)
  - Test window: 5 trading days (out-of-sample)
  - Purge gap: 2 trading days (prevent information leakage, per de Prado 2018)
  - Step: 5 trading days (non-overlapping test windows)
- **Tachyon SG Calibration**:
  - Parameter: `window_length` (5, 7, 9, 11, 13) and `polyorder` (2, 3, 4)
  - Metric: Information Coefficient = Spearman(SG_acceleration, forward_5bar_return)
  - Constraint: IC > 0.03 on test window (below this = noise)
  - Selection: Param combo with highest MEAN IC across all walk-forward folds, NOT max IC on any single fold
- **Hawkes Calibration**:
  - Parameters: `alpha` (excitation) and `beta` (decay), constrained by alpha/beta < 1 (stationarity)
  - Metric: IC = Spearman(hawkes_intensity_at_trade_entry, forward_30min_return_direction)
  - Grid: alpha in [0.1, 0.5] step 0.05, beta in [0.5, 5.0] step 0.25
  - Selection: Same mean-IC-across-folds protocol
- **Regime Conditioning**: Separate calibration for each volatility regime (LOW/MEDIUM/HIGH/SHOCK). Parameters that work in HIGH_VOL may be useless in LOW_VOL.
- **Output**: Writes calibrated parameters to `config/calibrated_params.yaml` with metadata (IC score, fold count, date range, regime). Tachyon and Exhaustion Monitor read from this file at startup.
- **Staleness Guard**: If calibrated params are >10 trading days old, system falls back to conservative defaults (wider SG window, faster Hawkes decay).

**Why Information Coefficient?**: Traditional backtesting optimizes on realized PnL, which conflates signal quality with position sizing, slippage, and luck. IC isolates the predictive power of the signal itself. A high-PnL parameter set with IC < 0.02 is curve-fit noise. A moderate-PnL parameter set with IC > 0.05 has genuine predictive power.

#### 2E.5: Chaos Drill Checklist {#section-2e5}

**5 fault injection tests that MUST pass before live deployment. Each drill simulates a specific catastrophic failure and verifies the circuit breaker trips correctly.**

| # | Drill | Injection | Expected Behavior | Pass Criteria |
|---|-------|-----------|-------------------|---------------|
| CD-01 | **Pandas Fat Finger** | Inject `time.sleep(0.2)` inside Brain's indicator computation loop (simulates 200ms GIL block) | AsyncioHeartbeat detects >50ms lag, trips `brain_circuit_breaker`, Muscle continues stop monitoring autonomously | Heartbeat alert fires within 60ms of injection. No stop-monitoring gaps. Brain resumes after Muscle confirms stops active. |
| CD-02 | **Toxic Tsunami** | Configure SyntheticBroker with 90% adverse selection rate. Run Ghost-Maker against it for 100 trades. | Ghost-Maker Toxicity Score should spike >70 within 3 fills. System should abort pegging and escalate to spread-cross or cancel. | >80% of trades abort before 5th re-peg. Average adverse excursion post-fill < 25 bps. |
| CD-03 | **Phantom Fill** | SyntheticBroker reports a fill via callback, but the fill message is silently dropped before reaching local state. | ReconciliationAuditor detects broker-shows-position vs local-shows-flat mismatch at next 5-min check. | Mismatch detected within 5 minutes. SIGKILL issued. MOC orders sent via broker API. Kill switch set in Redis. |
| CD-04 | **Adverse Selection Sniper** | Configure SyntheticBroker so that every fill is followed by a 30 bps adverse move within 500ms. | Ghost-Maker's AdverseSelectionAudit tracks post-fill direction rate. After 5 consecutive adverse fills, system should auto-reduce size or halt ticker. | Adverse fill rate tracked correctly. Circuit breaker fires after configurable N consecutive adverse fills. |
| CD-05 | **Redis Lobotomy** | Kill Redis container (`docker stop nzt48-redis`) while a position is open and the Muscle is monitoring stops. | Disruptor Engine detects Redis failure within 3 heartbeats. StateManager falls back to in-memory cache. Muscle continues stop monitoring on cached state. No orphan positions. | No unmonitored position gap > 500ms. StateManager logs Redis failure + fallback. System resumes when Redis returns. |

**EXECUTION ORDER**: CD-05 (Redis Lobotomy) first — this is the most dangerous failure mode. Then CD-01 (GIL), CD-03 (Phantom Fill), CD-02 (Toxic Tsunami), CD-04 (Adverse Selection).

**Go-Live Gate Addition**: ALL 5 chaos drills must pass with 100% success rate across 3 consecutive runs before the 63-Day Paper Gauntlet begins.

---

### Section 2F: QUANTUM APEX ROADMAP -- Gemini Adversarial Review Integration {#section-2f}

**CONTEXT**: After completing v15.5, a comprehensive adversarial review was conducted by 4 personas (Chief Quant, Lead Systems Architect, CRO, Academic Reviewer) evaluating every strength and weakness of the full AEGIS architecture. The review identified 5 critical infrastructure flaws that, if left unaddressed, will mathematically destroy the system regardless of alpha quality. This section triages the findings into actionable phases.

**THE 5 CRITICAL INFRASTRUCTURE FLAWS**:

| # | Flaw | Root Cause | Impact | Fix |
|---|------|-----------|--------|-----|
| F-05 | **Data Feed Inadequacy** | ~~yfinance is a REST scraper with 1-3s delay~~ **RESOLVED**: IBKR IB Gateway now primary data source via `ibkr_source.py` + Docker container. Real-time L1 quotes, official OHLCV bars. yfinance retained as fallback only. L2 LOB data available in Phase Q2 via `ib.reqMktDepth()`. | Paper-trading now uses real-time IBKR data. Microstructure strategies (Phase Q2) will use L2 when implemented. | **GA-01**: ~~Integrate IBKR TWS API~~ **DONE** (v16.1). `data_hub/sources/ibkr_source.py` + `docker-compose.yml` ib-gateway service. |
| F-06 | **SQLite Contention** | File-based DB with single-writer lock. Brain, Muscle, Auditor all need concurrent read/write. | `database is locked` exceptions during emergency flatten = terminal failure. | **GA-02**: Migrate to PostgreSQL WAL mode (concurrent R/W) or use in-memory ring buffer + async write queue. |
| F-07 | **AWS CPU Steal-Time** | t3.small is shared tenancy with burstable CPU. Hypervisor steals 20-50ms of CPU time unpredictably. | Ghost-Maker cancellation arrives 30ms late = catch falling knife on 3x leveraged ETP. | **GA-15**: Migrate to c7g.medium dedicated host in eu-west-2 (London). |
| F-08 | **Fixed Commission Drag** | At GBP 5/trade on GBP 2,500 sub-positions: 0.40% round-trip drag. Ghost-Maker captures 0.20% spread. Net EV = NEGATIVE. | Multi-trade strategy is mathematically unprofitable at current capital level. | **GA-04**: IBKR Tiered (0.05%, GBP 1.00 min). MAX_CONCURRENT=1 until equity > GBP 25k. |
| F-09 | **MOC Slippage in Vacuum** | ReconciliationAuditor fires MOC during flash crash. MM unplugged = spread at infinity. MOC fills 10-25% away. | Emergency safety net becomes the disaster. Single trade can destroy months of compounding. | **GA-05**: Spread-Expansion Circuit Breaker. Forbid Market Orders when spread > 50bps. Use passive Limit + inverse ETP hedge. |

#### 2F.1: Execution Physics Upgrades (Actionable Now) {#section-2f1}

These upgrades are implementable within the current Python/Docker/EC2 architecture:

**1. Synthetic Fair Value (SFV) Arbitrage Engine (GA-08)**
- Compute continuous real-time fair value: `SFV = NQ_futures * leverage_factor * (GBP/USD) - overnight_swap_accrual`
- When SFV diverges from LSE ETP Ask by >2 ticks, the MM's repricing algorithm is lagging
- Fire aggressive IOC order into the lagging quote before the MM updates
- Requires GA-01 (WebSocket feed) as prerequisite
- Reference: The MM does not have an independent opinion on ETP price; they derive it from US futures. This is deterministic, not predictive.

**2. ProcessPoolExecutor Brain Isolation (GA-03)**
- Current Disruptor Engine uses asyncio coroutines sharing the same GIL
- Must upgrade to `multiprocessing.Process` or `concurrent.futures.ProcessPoolExecutor`
- Brain runs in separate OS process (Core 1), Muscle stays in asyncio (Core 2)
- Communication via `multiprocessing.Queue` or ZeroMQ IPC socket
- Eliminates GIL contention entirely (not just detects it like AsyncioHeartbeat)
- AsyncioHeartbeat becomes the BACKUP safety net, not the primary defense

**3. Single-Writer Actor Model for Broker API (GA-07)**
- Only ONE coroutine (Execution Dispatcher) is legally allowed to talk to the broker API
- All subsystems drop commands into `asyncio.PriorityQueue`:
  - Priority 0: EMERGENCY_FLATTEN (CDaR / GIL Heartbeat / ReconciliationAuditor)
  - Priority 1: TOXICITY_CANCEL (Ghost-Maker evasion)
  - Priority 2: HAWKES_EXIT (take profit)
  - Priority 3: TACHYON_ENTRY (snipe)
- If Dispatcher pulls P0 command, instantly set TICKER_LOCKED state
- When P2 Hawkes exit arrives 1ms later for same ticker, Dispatcher sees lock and drops duplicate
- Eliminates race condition where two conflicting commands hit broker simultaneously

**4. Token Bucket API Rate Limiter (GA-06)**
- Local `TokenBucket` class mirrors broker's exact rate-limit algorithm
- Before every API call, Muscle requests a token. If denied, execution downgrades:
  - >80% consumed: Ghost-Maker peg timeout 800ms -> 3000ms (passive mode)
  - >90% consumed: Only emergency flatten commands allowed
  - 100% consumed: System enters HALT state, cancels all resting orders
- Always reserve 20% of API capacity for emergency flatten operations

**5. Spoof Detection Radar (GA-11)**
- Track order cancellation rates per price level
- If an order >5x average book size appears and disappears within 500ms without filling:
  - Tag the order book as "SPOOFED"
  - Halt all Ghost-Maker execution for 3 seconds
  - Log spoof event with timestamp, size, price level for post-trade analysis
- Prevents the system from being baited by MM phantom liquidity

**6. Spread-Expansion Circuit Breaker (GA-05)**
- If bid-ask spread exceeds 50bps at any point during market hours:
  - Forbid ALL Market Orders (including emergency MOC)
  - ReconciliationAuditor's emergency exit uses passive Limit Order pegged to Mid-Price
  - If spread exceeds 100bps: consider purchasing inverse ETP as delta-neutral hedge until liquidity returns
  - If spread exceeds 200bps: system enters HALT state (MM has unplugged; no safe exit exists)

#### 2F.2: Capital Discipline Rules (GA-04) {#section-2f2}

**The £10k Death Zone**: With GBP 10,000 equity, fixed broker costs mathematically destroy multi-trade strategies.

| Equity Level | Max Concurrent Positions | Min Position Size | Commission Cap (IBKR Tiered) |
|-------------|--------------------------|-------------------|------------------------------|
| < GBP 10,000 | 1 | GBP 10,000 | 0.05% (GBP 5.00) |
| GBP 10,000 - 25,000 | 1 | Full Kelly allocation | 0.05% (GBP 5.00 - 12.50) |
| GBP 25,000 - 50,000 | 2 | GBP 12,500+ per position | 0.04% (GBP 5.00 - 12.50) |
| GBP 50,000+ | 4 (full unlock) | GBP 12,500+ per position | 0.03% (converging to institutional) |

**Rule**: Do NOT dilute capital. Fire maximum Kelly-allowed allocation in ONE single bullet until equity crosses GBP 25,000. Wait for the absolute highest-conviction Tachyon+Proxy alignment across the full universe. One perfect trade beats three mediocre ones when commissions are the binding constraint.

#### 2F.3: Quantum Apex Phase Gates (Long-Term Roadmap) {#section-2f3}

**Phase Q1 (Current -- Python Metal)**:
- Complete all v15.5 Crucible modules (CR-01 through CR-04)
- Implement GA-01 through GA-05 (critical infrastructure fixes)
- Implement GA-06 through GA-15 (execution physics upgrades)
- Run 63-Day Paper Gauntlet with real WebSocket data feed
- Target: >40% WR (Wilson Score Lower Bound), <500ms signal-to-order

**Phase Q2 (GBP 25k -- Bare Metal)**:
- Migrate to c7g.medium dedicated host in eu-west-2 (London)
- Implement TCP_NODELAY + TCP_QUICKACK on all broker sockets
- Migrate SQLite to PostgreSQL with WAL mode
- Unlock MAX_CONCURRENT_POSITIONS=2
- Implement Nightly Combine genetic optimization (Lambda)

**Phase Q3 (GBP 50k -- Rust Execution Core)**:
- Rewrite ExecutionMuscle in Rust using PyO3 FFI
- Replace asyncio networking with Rust io_uring or DPDK kernel bypass
- Implement POSIX Shared Memory (/dev/shm) Lock-Free Ring Buffer for Brain-Muscle IPC
- Latency target: <50 microseconds signal-to-wire
- Unlock MAX_CONCURRENT_POSITIONS=4

**Phase Q4 (GBP 100k+ -- Institutional Metal)**:
- Neural Compound Hawkes Processes (LSTM) for exit timing
- Deep Q-Network (DQN) Execution Agent replacing Ghost-Maker static state machine
- Cross-Impact OFI Tensors (multi-asset order flow propagation)
- Fractional Differentiation on all ML input features
- Adversarial Reinforcement Learning (ARL) for MM toxicity evasion training
- Continuous FIX Drop-Copy reconciliation (replace 5-min polling)

**CRITICAL**: Phase Q1 is the ONLY phase that matters now. Do not architect for Phase Q4 until Phase Q1 proves >40% WR on live paper trading with real data. Premature optimization is the root of all evil (Knuth, 1974).

#### 2F.4: Competitive Intelligence (Commercial Bot Teardown) {#section-2f4}

Analysis of Tickeron Holly, Trade Ideas, Kavout K-Score, and similar commercial platforms:

**What they do well (steal this)**:
- **Tickeron's Probabilistic Matrices**: Assigns literal percentage probability per setup based on historical fingerprint matching. AEGIS should implement a Setup Fingerprint Matrix: exact combination of RVOL + Time-of-Day + VIX + Regime = historical win probability -> dynamic Kelly scaling.
- **Holly's Risk-On/Risk-Off Dual Mode**: If AEGIS reaches exhaustion target but NQ=F tape momentum is accelerating, dynamically switch to "Risk-On" -- widen Hawkes exhaustion threshold by 2x, let the runner run.
- **Holly's Nightly Genetic Combine**: After market close, run millions of parameter permutations. Only top-5 surviving parameter sets loaded for next morning. AEGIS equivalent = GA-14.
- **Kavout K-Score Ensemble Veto**: If ANY single sub-model detects severe anomaly, trade is vetoed regardless of aggregate score. AEGIS equivalent: LOB Toxicity Veto (if OBI shows 80% volume hitting bid despite bullish S15 score, STAND DOWN).

**What they do badly (avoid this)**:
- ALL commercial bots issue signals to thousands of users simultaneously
- Thousands of market orders hit the exchange at the same millisecond
- MM sees the flow, widens spread, fills everyone at premium
- AEGIS advantage: Ghost-Maker places a resting limit order BEFORE the retail wave. When the commercial bot users fire their market orders, they crash into YOUR limit order. You become the synthetic market maker. You capture THEIR slippage.

**The 14:30 Volatility Funnel**: The LSE closes at 16:30 UK, but the US opens at 14:30 UK. This 2-hour overlap is where 80% of LSE ETP volume occurs. AEGIS must concentrate 90% of its Kelly sizing into this window. Ignore morning chop; trade the transatlantic liquidity explosion.

#### 2F.5: Strengthened Defenses (Fixing Every "Why It Won't Work") {#section-2f5}

| # | Failure Vector | The Fix | Priority |
|---|---------------|---------|----------|
| 1 | Partial-Fill Commission Trap (12 shares = GBP 1.00 min) | Use MinQty/All-Or-None routing flags. Refuse fills below minimum viable lot size. | P1 |
| 2 | Variance Drag in sideways markets | GA-12: If 5d ATR < 20d ATR, ban 3x/5x ETPs. Cash or 1x only. | P1 |
| 3 | UK vs US market hours asymmetry | Concentrate 90% Kelly into 14:30-16:20 UK overlap window. | P1 |
| 4 | GBP/USD flash crash poisoning SFV | GA-13: If cable moves >0.25% in 60s, disable SFV arbitrage entirely. | P1 |
| 5 | FIFO Queue Hallucination in backtests | Ghost-Maker MUST peg Bid+1 (new price level = front of queue). Never join existing queue. | P0 (in CR-01) |
| 6 | ML dimensionality curse (500 rows) | Purged Combinatorial CV (CPCV) with 5-day purge/embargo. max_depth=2 on all trees. | P1 |
| 7 | Hawkes baseline shifts on regime change | Online learning: Hawkes baseline = EMA(intensity, 60 min window). Auto-recalibrates intraday. | P1 |
| 8 | Epps Effect false spikes in proxy math | Tick-Time Interpolation: sample by Volume Clocks (per 10k shares) not wall clocks. Erases asynchronous noise. | P2 |
| 9 | Dark Pool volume obfuscation | Subscribe to broker "Unfiltered" or Odd-Lot data feed. Incorporate into Tachyon acceleration filter. | P2 |
| 10 | NTP clock drift breaking Lead-Lag timestamps | Deploy IEEE 1588 PTP (Precision Time Protocol) for sub-microsecond sync. | Phase Q2 |
| 11 | BGP route leaks / network path corruption | Multi-path redundancy: primary IBKR + fallback Polygon.io feed. Divergence = halt. | Phase Q2 |
| 12 | Redis OOM kills during telemetry flood | Redis maxmemory policy = allkeys-lru. Telemetry is evictable; position state is not. | P1 |
| 13 | Weaponized 3-Strikes by adversarial MM | Poisson-randomized re-peg timer (50ms-1200ms). MM cannot hunt a random rhythm. | P1 |
| 14 | IBKR intraday margin hikes | 50% Heat Shield: max 50% equity deployed. 50% cash absorbs any margin requirement change. | P1 (already in Constitution) |
| 15 | Single-issuer counterparty default | Cross-Issuer Capital Splitting: GBP 5k WisdomTree + GBP 5k LeverageShares for same underlying. | P2 |

---

### Section 2G: ROUND 23 FORENSIC AUDIT -- Silent Killers & Abyss Analysis {#section-2g}

**CONTEXT**: After completing v15.6, a comprehensive forensic audit was executed across 4 directives: Conquest Analysis (78 mechanisms validated across 4 personas), Abyss Analysis (17 failure vectors identified), v16.0 Upgrade Path (Section 0/8 deltas), and Silent Killer Hunt (4 previously-unidentified catastrophic bugs found by cross-referencing plan against 18,000 lines of code). This section documents the findings that were triaged into P0/P1 items.

#### 2G.1: THE 4 SILENT KILLERS {#section-2g1}

These bugs were NOT identified in any previous review round (v10-v15.6). Each survived 15+ revisions because the defect spans multiple files/subsystems and only manifests under specific runtime conditions.

**SILENT KILLER #1: THE EQUITY DENOMINATOR PHANTOM (SK-01, P0)**

| Field | Value |
|-------|-------|
| File | `qualification/circuit_breakers.py:387` |
| Root Cause | `_starting_equity` frozen at system init, never updated |
| Trigger | Equity grows 50%+ from init, then routine 1.5% daily loss occurs |
| Consequence | 1.5% loss on 30K equity with 20K _starting_equity = 2.25% = false L2 trigger. Emergency flatten on normal drawdown. |
| Why Missed | Plan specifies drawdown % but never specifies denominator. Comment says "so threshold doesn't shift as we lose" -- reasonable for shrinking equity, catastrophic for growing equity. Never triggered in paper (0% WR = no equity growth). |
| Fix | (1) Change `reset_daily(self)` signature at circuit_breakers.py:298 to `reset_daily(self, current_equity: float)`. (2) Set `self._starting_equity = current_equity` inside reset_daily(). (3) Update caller in main.py to pass current equity. (4) Also fix `_starting_equity` in dynamic_sizer.py:188 and sheets_logger.py:67 (hardcoded 10000.0). Use SESSION-OPEN equity (anchored at daily reset), not live equity. |

**SILENT KILLER #2: THE ZOMBIE HALT (SK-02, P0)**

| Field | Value |
|-------|-------|
| File | `main.py:1176-1184` (TWO queries: virtual_trades at :1177 + trades fallback at :1183) + `delivery/database.py:1008-1022` (THIRD query: `get_consecutive_losses()`) + `circuit_breakers.py:298` (reset_daily) |
| Root Cause | THREE consecutive loss queries have NO date filter. reset_daily() clears in-memory state but _update_state_from_db() immediately reloads stale losses from ALL THREE unfiltered DB queries. |
| Trigger | 5 consecutive losses in one session, then next-day trading |
| Consequence | PERMANENT DEADLOCK. System halts on 5 losses. reset_daily() clears circuit breaker. Next scan reloads 5 losses from DB. ImmutableRiskRules blocks all trades. No trades = no wins = deadlock forever. Requires manual DB surgery. |
| Why Missed | Plan P0-9 worries about halts being LOST on restart. Nobody checked for halts being PERMANENT. Dual consecutive-loss trackers (circuit_breakers._consecutive_losses vs main._consecutive_losses) interact destructively. |
| Fix | Add `WHERE exit_time >= datetime('now', '-12 hours')` to ALL THREE consecutive loss queries: main.py:1177-1178 (virtual_trades), main.py:1183-1184 (trades fallback — also fix column from `time_entered` to `exit_time` for consistency), and delivery/database.py:1011-1022 (get_consecutive_losses()). |

**SILENT KILLER #3: THE CONFIDENCE CEILING (SK-03, P0)**

| Field | Value |
|-------|-------|
| File | `daily_target.py:71` (_MIN_CONFIDENCE = 75.0), `risk_sizer.py:45` (MIN_CONFIDENCE = 60), Plan Section 0.1 (65) |
| Root Cause | Three conflicting confidence minimums: Constitution=65, ImmutableRiskRules=60, S15=75 |
| Trigger | Every signal evaluation |
| Consequence | S15 rejects ~40% of signals that the Constitution considers valid. After ALL timing defects fixed, confidence ceiling will still suppress signal generation. ~100-200 missed trades/year at +0.4% = 40-80% missed annual return. |
| Why Missed | daily_target.py:71-72 has academic citation "Harvey & Liu (2015) multiple-testing correction" that reviewers accepted without checking Constitutional alignment. |
| Fix | Align _MIN_CONFIDENCE=65 to match Constitution R13. Or formally amend Constitution to 75 with documented justification. |

**SILENT KILLER #4: THE DUAL THROTTLE PARADOX (SK-04, P0)**

| Field | Value |
|-------|-------|
| File | `risk_sizer.py:362` (+2.0% halt) + `risk_sizer.py:370` (+1.5% halt — BOTH return halt:True) + `daily_target.py:70` (_MAX_SIGNALS_PER_DAY=1) + `daily_target.py:297,348,497` (_daily_signal_fired) |
| Root Cause | THREE independent throttles that form coupled constraint: SessionProtection halts at +1.5% (risk_sizer.py:370), _daily_signal_fired blocks after first signal (daily_target.py:348), AND _MAX_SIGNALS_PER_DAY=1 (daily_target.py:70) |
| Trigger | System achieves +1.5% daily gain on first trade |
| Consequence | Cannot fire second signal to capture remaining +0.5%. 2% daily target architecturally unreachable. Max achievable = +1.5%/day = (1.015)^252 = 42x vs (1.02)^252 = 147x. Costs 71% of theoretical ceiling. |
| Why Missed | P0-2 and T-08 listed as separate items in different files/modules. No reviewer traced their interaction. Must be fixed as coupled unit in same deployment. |
| Fix | Fix P0-2 (SessionProtection to +2.0%) AND T-08 (remove _daily_signal_fired) SIMULTANEOUSLY. |

#### 2G.2: ABYSS ANALYSIS -- CRITICAL FAILURE VECTORS {#section-2g2}

17 failure vectors identified. Top 6 CRITICAL vectors:

| # | Vector | Root Cause | Consequence | Fix |
|---|--------|-----------|-------------|-----|
| V1.1 | **ib_insync blocks event loop** | ib_insync's synchronous ib.sleep(0.5) calls occupy the asyncio event loop for 500ms+ | Ghost-Maker peg cancellation arrives 500ms late = catch falling knife on 3x ETP | Use ib_insync async mode. Replace all ib.sleep() with asyncio.sleep(). |
| V1.2 | **TWS API JVM overhead** | IBKR TWS adds 15-50ms JVM overhead per message. Ghost-Maker needs <10ms. | Dynamic pegging may not be viable on TWS. Consider IBKR Client Portal API or native socket. | Budget 25-50ms per order modification. Reduce max re-pegs from 5 to 3. |
| V2.1 | **PostgreSQL synchronous_commit=off** | Committed transactions lost on crash. ReconciliationAuditor issues SIGKILL. | Fill records vanish after SIGKILL = position state corruption | Use synchronous_commit=on for TRADE priority writes. =off only for TELEMETRY. |
| V4.1 | **ISA eligibility gate missing** | uk_isa/isa_eligibility.py DOES NOT EXIST | Single non-ISA trade voids entire tax wrapper. Irreversible legal/tax damage. | Implement before any live trading. Already P0-1. |
| V4.3 | **Signal queue write-only** | Queue(maxsize=50) with put_nowait() but NO consumer. Wrong exception class (asyncio.QueueFull vs queue.Full). | After 50 signals, scan cycle crashes. Before crash, all queued signals silently discarded. | Implement consumer coroutine OR remove queue entirely (signals processed inline). |
| V4.6 | **VIX default=0 (fail-open)** | When VIX fetch fails, default is 0 = maximum aggression, no risk controls | During network outage + market crash, system trades at max aggression. Worst behavior at worst time. | Change default to 99 (fail-closed, RISK_OFF). Already P0-6. |

**Additional HIGH vectors**: Confidence floor contradiction (75/65/60), dual daily-loss systems (ImmutableRiskRules 3% vs L1/L2/L3 cascade), circuit breaker restart bypass, GIL problem unsolvable in pure Python (GA-03 must be P0), overnight gap risk on 3x ETPs (P1-13 should be P0), settings.yaml timezone US/Eastern.

#### 2G.3: CONQUEST ANALYSIS SUMMARY {#section-2g3}

78 mechanisms audited across 4 personas. 97.4% correctly specified.

| Persona | Mechanisms | Correct | Flagged |
|---------|-----------|---------|---------|
| Chief Quant (CQO) | 20 mathematical | 19 | 1 (Thomas & Zhang citation context) |
| Lead Systems Architect (LSA) | 15 system components | 15 | 0 |
| Chief Risk Officer (CRO) | 18 risk controls | 18 | 0 |
| Academic Reviewer (AR) | 25 citations | 24 | 1 (Thomas & Zhang 2008 misapplied for lead-lag; should be Hasbrouck 2003) |

**Key validation**: Kelly fraction f*=0.280 VERIFIED. Constitutional hierarchy CORRECT. Disruptor Engine architecture CORRECT. All risk controls correctly designed (though many unimplemented). Academic grounding rigorous with only 1 misapplied citation.

**Critical gap**: The plan is 97.4% correct in DESIGN but 0% implemented in CODE. Of 83 stop-ship items, ZERO have been fixed. The system has 0% win rate across 52 paper trades. The plan correctly identifies this as execution timing (not signal quality) but progress requires CODE CHANGES, not more plan revisions.

**10 additional academic foundations recommended**: Cartea/Jaimungal/Penalva (2015) for execution, Artzner/Delbaen/Eber/Heath (1999) for CVaR coherence, Rockafellar/Uryasev (2002) for CVaR optimization, Engle (2002) DCC-GARCH for correlation, Ledoit/Wolf (2004) for covariance estimation, Moustakides (1986) for CUSUM optimality, Gatheral (2010) for impact model, Hasbrouck (2003) for lead-lag (replaces Thomas & Zhang), Hansen/Lunde/Nason (2011) Model Confidence Set, White (2000) Reality Check for data snooping.

#### 2G.4: SCOPE CREEP WARNING {#section-2g4}

**META-FINDING FROM ABYSS ANALYSIS**: The plan has grown from v10 to v15.7 through 23 review rounds. Each round adds complexity. Stop-ship items grew: 0 -> 18 -> 23 -> 27 -> 32 -> 36 P0 items. Implementation: ZERO items fixed.

**MANDATORY DIRECTIVE**: FREEZE THE PLAN after v15.8. No more review rounds until at least T-01 through T-08 are implemented and 48h paper validation shows improved win rate. The perfect plan that never ships is worth less than an imperfect system that trades.

**Execution Priority** (if going live tomorrow, 5 fixes totalling ~14.5 hours):
1. Fix VIX default to 99 (30 seconds)
2. Enforce overnight kill for ALL ETPs (15 minutes)
3. Persist circuit breaker state + fix equity denominator (2 hours)
4. Implement ISA eligibility gate (8 hours)
5. Fix ib_insync async mode (4 hours)

---

# SECTION 2H: RUNTIME INVARIANT CONTRACT {#section-2h}

**Origin**: Gemini "Paper Architecture Syndrome" diagnosis (v15.8). The system has 86 stop-ship items with ZERO implemented in code. 0% win rate across 52 paper trades. Gemini proposed formalizing all critical risk controls as **boolean invariants** — predicates that are either TRUE (system may trade) or FALSE (`sys.exit(1)`). This is Design-by-Contract applied to trading infrastructure.

**Philosophy**: Every risk control that exists only as a plan bullet point is a fiction. An invariant is real only when it has: (1) a boolean predicate in code, (2) an enforcement point that runs at a specific time, (3) a log path for audit, and (4) an acceptance test that proves the kill switch works.

---

#### 2H.1: THE 12 HARD RUNTIME INVARIANTS {#section-2h1}

| # | Name | Predicate (Boolean) | Evidence / Log Path | Enforcement Point | Acceptance Test |
|---|------|---------------------|--------------------|--------------------|-----------------|
| 1 | **IMAGE_PARITY** ⭐P0 | `env.IMAGE_DIGEST == git.HEAD_SHA` | `logs/system.log "BOOT_STRAP_PARITY"` | `main.py` Global Init | `test_deploy_parity`: Assert mismatch → `sys.exit(1)` |
| 2 | **ISA_FAIL_CLOSED** | `ticker.is_isa_eligible == True` | `logs/trades.log "ELIGIBILITY_CHECK"` | `uk_isa/isa_eligibility.py` pre-order | `test_isa_gate`: Non-ISA ticker → order rejected, logged |
| 3 | **VIX_FAIL_CLOSED** | `vix_value != 0 AND vix_age < 300s` | `logs/risk.log "VIX_HEALTH"` | `feeds/market_structure.py` every scan | `test_vix_default`: Missing VIX → regime=RISK_OFF, VIX=99 |
| 4 | **DRAWDOWN_CASCADE** | `daily_pnl > L3_threshold` | `logs/risk.log "CIRCUIT_BREAKER"` | `qualification/circuit_breakers.py` tick-level | `test_cascade`: Inject -4% → verify L1→L2→L3 fire in order |
| 5 | **POSITION_LIMIT** | `open_positions <= MAX_CONCURRENT` | `logs/trades.log "POSITION_GATE"` | `qualification/risk_sizer.py` pre-order | `test_position_limit`: At max → new order rejected |
| 6 | **OVERNIGHT_FLAT** | `positions.count() == 0 at 16:25 GMT` | `logs/trades.log "EOD_FLATTEN"` | `main.py` scheduled job | `test_overnight`: Position exists at 16:30 → verify MOC sent by 16:25 |
| 7 | **EQUITY_FRESH** | `_current_equity == broker.equity (±0.1%)` | `logs/risk.log "EQUITY_RECONCILE"` | `core/reconciliation_auditor.py` 5-min | `test_equity_stale`: Freeze broker feed → verify halt after 5 min |
| 8 | **CONFIDENCE_FLOOR** | `signal.confidence >= 65` (Constitution) | `logs/signals.log "CONFIDENCE_GATE"` | `strategies/daily_target.py` signal emit | `test_confidence`: Signal at 64 → rejected. Signal at 65 → passed |
| 9 | **IMMUTABLE_RISK** | `ImmutableRiskRules.__setattr__` raises | `logs/risk.log "IMMUTABLE_GUARD"` | `qualification/risk_sizer.py` class init | `test_immutable`: Attempt `rules.MAX_RISK = 99` → `AttributeError` |
| 10 | **HALT_PERSISTENCE** | `redis.get("circuit_halt") survives restart` | `logs/risk.log "HALT_PERSIST"` | `qualification/circuit_breakers.py` init | `test_halt_persist`: Set halt → restart Docker → verify halt still active |
| 11 | **LOSS_STREAK_SCOPED** | `consecutive_loss_query.WHERE date >= session_start` | `logs/risk.log "LOSS_STREAK"` | `main.py` loss counter | `test_zombie_halt`: Insert 5 old losses → verify NOT counted in today's streak |
| 12 | **DATA_FEED_ALIVE** | `last_tick_age < MAX_STALE_SEC AND tick_count > MIN_TICKS` | `logs/feeds.log "FEED_HEALTH"` | `feeds/market_data.py` every 60s | `test_feed_stale`: Freeze feed → verify trading halted after MAX_STALE_SEC |

⭐ = New P0 item added by this section. All others formalize EXISTING P0/P1 items as testable predicates.

---

#### 2H.2: INVARIANT ENFORCER MODULE {#section-2h2}

**New file**: `core/invariant_enforcer.py`

**Responsibilities**:
1. Run ALL 12 invariants at boot (fail = `sys.exit(1)`, no trading with broken invariants)
2. Run invariants 2-12 every 60 seconds during market hours
3. Log every check result to `logs/invariants.log` with timestamp + pass/fail
4. On ANY invariant failure during runtime: trigger L3 flatten + alert + halt
5. Expose `/api/invariants` endpoint returning current status of all 12

**Implementation pattern**:
```
class InvariantEnforcer:
    invariants: List[Callable[[], bool]]  # Each returns True=OK, False=KILL

    def check_all(self) -> dict:
        results = {inv.__name__: inv() for inv in self.invariants}
        failures = [k for k, v in results.items() if not v]
        if failures:
            log.critical(f"INVARIANT FAILURE: {failures}")
            self.kill_switch.activate(reason=f"Invariant: {failures}")
        return results
```

**Estimated implementation**: 6 hours (module + 12 acceptance tests + integration with scheduler)

---

#### 2H.3: PAPER ARCHITECTURE SYNDROME — DIAGNOSIS ACCEPTED {#section-2h3}

Gemini's diagnosis is correct: the plan has grown to 86 stop-ship items across 8 sections with ZERO items implemented in code. The cure is NOT more planning — it is implementation. The Runtime Invariant Contract (this section) is the LAST planning addition. Every invariant above maps to an existing P0/P1 item. The invariants do not create new work — they formalize the acceptance criteria for work already on the list.

**Triage of Gemini's 12 proposed invariants**:
- 10 of 12 were ALREADY COVERED by existing P0/P1 items (R21-19, R21-06, R21-12, R21-16, R21-18, SK-01, SK-02, SK-03, SK-04, T-05)
- 1 genuinely new: IMAGE_PARITY (→ RI-01, P0)
- 1 partially covered: DATA_FEED_ALIVE (→ RI-03, P1, extends existing feed monitoring)
- Net new work: ~8 hours (IMAGE_PARITY gate + InvariantEnforcer module + data feed staleness)

---

#### 2H.4: FINAL PLAN FREEZE DIRECTIVE {#section-2h4}

**v16.0 IS THE ABSOLUTELY FINAL PLAN VERSION.** (Updated from v15.9 — see Section 2J for v16.0 End-State Architecture.)

The plan is complete. 89 stop-ship items are catalogued, prioritized, and have acceptance criteria. Adding more items will not improve the system. Only CODE improves the system.

**Implementation order** (strict — do NOT skip ahead, superseded by Section 2I.6):
1. **T-08+SK-04** (coupled fix first), then T-01, T-02, T-04, **T-05** (before T-06/T-07), T-06, T-07, T-03, T-10 — 24h
2. **SK-01, SK-02, SK-03** (SK-04 done in step 1) — 5h
3. **R21-19** (ISA Eligibility Gate) — legal requirement — 8h
4. **RI-01** (IMAGE_PARITY) — deploy safety gate — 2h
5. **R21-06** (VIX fail-closed) — 30 seconds
6. **R21-42** and remaining P0 items — in order of risk — ~40h
7. **Chaos Drills** (CR-05 through CR-09) — 20h
8. **63-Day Paper Gauntlet** — MTRL validation — 63 calendar days
9. **RI-02** (InvariantEnforcer module) — formalizes all invariants — 6h
10. **P1 items** — in priority order — ~80h

**Total estimated implementation**: ~250 hours + 63 calendar days of paper trading.

**ZERO more planning until Step 1 is complete.**

---

# SECTION 2I: THE RECKONING — COMPLEXITY REDUCTION & REALITY CHECK {#section-2i}

**Origin**: v15.9. Combined Gemini 4-persona re-review (P1 Chief Quant, P2 Lead Systems Architect, P3 CRO, P4 Academic Reviewer) with Claude's honest meta-assessment of the plan itself.

**The diagnosis is now unanimous across all three AI reviewers (Gemini, Claude, GPT)**: The plan is a masterpiece of trading system *design* and a textbook case of analysis paralysis. 23 review rounds. 2,000+ lines. 89 stop-ship items. 0% win rate. 0 items implemented.

---

#### 2I.1: GEMINI RE-REVIEW TRIAGE {#section-2i1}

Gemini submitted 25 specific findings across 4 personas. **Every single finding was already covered by an existing P0 or P1 item.** This is significant — it means the plan's *diagnostic completeness* is validated. The problem is not that we've missed anything. The problem is that we haven't built anything.

| Gemini Finding | Already Covered By | Status |
|---|---|---|
| P1-GREEN: Profit Ladder math | Section 2, Kelly math | ✅ Covered |
| P1-GREEN: SFV Arbitrage | GA-08 | ✅ Covered |
| P1-GREEN: Micro-Price OBI | GA-09 | ✅ Covered |
| P1-GREEN: 14:30 Volatility Funnel | Section 2F | ✅ Covered |
| P1-RED: Capital Drag Death Zone | GA-04 (P0-31) | ✅ Covered |
| P1-RED: Variance Drag | GA-12 (P1-39) | ✅ Covered |
| P1-RED: Dual Throttle Paradox | SK-04 (P0-36) | ✅ Covered |
| P2-GREEN: Disruptor isolation | Section 2D | ✅ Covered |
| P2-GREEN: Single-Writer Actor | GA-07 (P1-34) | ✅ Covered |
| P2-GREEN: IMAGE_PARITY | RI-01 (P0-37) | ✅ Covered |
| P2-RED: Python GIL Freeze | GA-03 (P0-30) | ✅ Covered |
| P2-RED: SQLite contention | GA-02 (P0-29) | ✅ Covered |
| P2-RED: yfinance hallucination | GA-01 (P0-28) | ✅ Covered |
| P2-RED: ib_insync blocking | AB-03 (P1-45) | ✅ Covered |
| P3-GREEN: ReconciliationAuditor | CR-03 (P0-26) | ✅ Covered |
| P3-GREEN: AsyncioHeartbeat | CR-02 (P0-25) | ✅ Covered |
| P3-GREEN: Chaos Drills | CR-05 to CR-09 (P1-28 to 32) | ✅ Covered |
| P3-RED: MOC slippage | GA-05 (P0-32) | ✅ Covered |
| P3-RED: Zombie Halt | SK-02 (P0-34) | ✅ Covered |
| P3-RED: Equity Denominator | SK-01 (P0-33) | ✅ Covered |
| P3-RED: VIX fail-open | R21-42 (P0-6) | ✅ Covered |
| P4-GREEN: MicrostructureCalibrator | CR-04 (P0-27) | ✅ Covered |
| P4-GREEN: SyntheticBroker | CR-01 (P0-24) | ✅ Covered |
| P4-GREEN: Bayesian Stranger Penalty | Section 5 (U-01) | ✅ Covered |
| P4-RED: Confidence Ceiling | SK-03 (P0-35) | ✅ Covered |
| P4-RED: ML Feature Leakage | R21-30 (P1-14) | ✅ Covered |
| P4-RED: ML Dimensionality Curse | AR-03 (P0-22) partial | 🟡 Enhanced → RK-03 |

**Result**: 25/25 findings already in plan. 1 enhancement extracted (CPCV + depth limit → RK-03). **The plan is diagnostically complete. The ONLY remaining problem is implementation velocity.**

---

#### 2I.2: CLAUDE HONEST ASSESSMENT — THE HARD TRUTHS {#section-2i2}

**Truth 1: The plan is self-defeating in its complexity.**

The system trades 12 leveraged ETPs on a £10k ISA account via a t3.small EC2 instance using IBKR real-time data (yfinance as fallback). The plan describes infrastructure appropriate for a $50M multi-strategy fund: Ghost-Maker Dynamic Pegging, Tachyon Acceleration Triggers, LMAX Disruptor Patterns, Hawkes Self-Exciting Processes, Synthetic Fair Value Arbitrage, Cross-Impact OFI Tensors, Rust FFI execution cores.

Ghost-Maker dynamic pegging is irrelevant when trading ETPs with £5M daily volume through a retail IBKR account. Cross-Impact OFI Tensors don't matter when placing one trade a day. The Rust FFI "Quantum Apex Roadmap" is for a system that doesn't have a working Python prototype.

**Truth 2: The 2% daily compound target has never been achieved.**

(1.02)^252 = 14,757% annualised. No systematic fund in recorded history has sustained this. Renaissance Technologies' Medallion Fund — the most successful quant fund ever — averaged ~66% annually (before fees). The plan's entire identity ("Compounding Machine", "2% Daily Target") is built on a mathematical ceiling, not a realistic goal.

**A system averaging 0.3-0.5% daily net (145-348% annualised) would be world-class.** This is the MVP target.

**Truth 3: Every hour spent planning is an hour not coding.**

The plan has been reviewed by Claude, Gemini, and GPT across 23+ rounds through 12+ fictional personas. Each round found more things wrong and added more items. Stop-ship items grew: 0 → 18 → 23 → 27 → 32 → 36 → 37 → 37 → 37. Implementation: ZERO items fixed.

---

#### 2I.3: COMPLEXITY REDUCTION MANDATE {#section-2i3}

**PHASE Q1 SCOPE (The Only Thing That Matters Now)**:

Phase Q1 is ONLY:
1. T-01 through T-08 (timing fixes) — 24h
2. **100-TRADE VALIDATION GATE** (RK-01) — ~2-4 weeks of paper trading
3. SK-01 through SK-04 (silent killers) — 8h — ONLY if WR >= 40% at gate
4. R21-19 (ISA gate) — 8h
5. Basic risk P0s (VIX fail-closed, ImmutableRiskRules guard, circuit breaker persistence) — 10h
6. 63-Day Paper Gauntlet — 63 calendar days

**DEFERRED TO PHASE Q2+** (do NOT touch until Phase Q1 gauntlet passes):
- All Section 2C items (institutional microstructure defenses)
- All Section 2D items (apex modules: Tachyon, Ghost-Maker, Lead-Lag, Exhaustion, Disruptor)
- Section 2E P1 items only: SyntheticBroker (CR-01 full module), MicrostructureCalibrator (CR-04), all Chaos Drills (CR-05 to CR-09). **NOTE**: CR-02 (AsyncioHeartbeat) and CR-03 (ReconciliationAuditor) remain P0 for Phase Q1 but in BASIC form — simple event loop lag monitor (CR-02) and simple broker API position check (CR-03). The full async hardening modules described in Section 2E are Phase Q2+.
- All Section 2F items (SFV Arbitrage, ProcessPoolExecutor, Token Bucket, Spoof Radar)
- GA-01 (WebSocket feed), GA-02 (PostgreSQL), GA-03 (ProcessPool), GA-15 (Bare Metal migration)
- All chaos drills (CR-05 through CR-09)

**Why**: These all require infrastructure that doesn't exist (WebSocket feeds, PostgreSQL, process isolation). Building them before proving the core signal engine works is building a Ferrari chassis around an engine that won't start.

**The items that remain in Phase Q1 total approximately 50 hours of coding + 100 paper trades + 63-day gauntlet.** This is achievable. 250 hours + 5 apex modules is not.

---

#### 2I.4: THE 100-TRADE VALIDATION GATE {#section-2i4}

After T-01 through T-08 are implemented, the system must execute exactly 100 paper trades before ANY further work proceeds.

**Metrics to capture**:
| Metric | PASS Threshold | FAIL Action |
|--------|---------------|-------------|
| Win Rate | >= 40% | STOP. Rework S15 signal logic (indicator weights, RVOL thresholds, confidence scoring). Do NOT proceed to SK fixes. |
| Average Winner | >= +1.5% | Review profit ladder rung placement |
| Average Loser | <= -1.5% | Review stop-loss ATR multiplier |
| Sharpe (annualised) | >= 1.0 | Review regime filter effectiveness |
| Max Consecutive Losses | <= 7 | Review correlation/clustering logic |
| Avg Holding Time | 15min - 4h | Review entry timing (too early = noise, too late = v15.3 problem) |

**If WR >= 40%**: Signal engine is viable. Proceed to SK-01 through SK-04, then remaining P0s, then 63-day gauntlet.

**If WR < 40%**: Signal engine needs fundamental changes. Do NOT throw infrastructure at a broken signal. Possible root causes:
- Indicator weights still wrong (T-05 implementation quality)
- RVOL thresholds too aggressive or too loose
- Confidence scoring model flawed
- Market regime detection broken (R21-11, all regimes = -1)
- LSE leveraged ETPs may not exhibit exploitable intraday momentum at this capital scale

---

#### 2I.5: ML HARDENING — CPCV + DEPTH LIMIT {#section-2i5}

Enhancement to AR-03 (walk-forward validation). The current ML meta-model trains LightGBM on ~413 historical trades. This is critically below the ~2,000 sample minimum for reliable gradient boosting ensembles.

**Mandatory constraints** (until N > 2,000 trades):
1. **Purged Combinatorial Cross-Validation (CPCV)** — de Prado (2018). Unlike k-fold, CPCV respects temporal ordering and purges overlapping samples. 5-day purge window + 5-day embargo window.
2. **max_depth=2** on all LightGBM trees — Forces the model to learn broad market regime rules (2 splits = 4 leaf nodes), not individual trade patterns. At N=413, deeper trees will memorize noise.
3. **Hard-prune `confidence` from feature_cols** — Confidence is the output the model validates. Including it as input creates systemic look-ahead bias. Replace with `raw_indicator_count`.
4. **ML remains in BYPASS mode** (P1-15) until N > 500 AND Deflated Sharpe Ratio > 1.0. **WARNING (Gemini Q9)**: Current trade count N=52 makes ANY cross-validation method (including CPCV) catastrophically overfit. With 52 observations and 5-fold CPCV, each test fold has ~10 trades — statistically meaningless. Do NOT attempt CPCV on Phase Q1 data. BYPASS mode is mandatory until N > 500. The N > 500 gate is a HARD INVARIANT, not a guideline.

---

#### 2I.6: ABSOLUTELY FINAL IMPLEMENTATION ORDER {#section-2i6}

This supersedes Section 2H.4.

| Step | Items | Hours | Gate |
|------|-------|-------|------|
| 1 | T-08+SK-04 (coupled: remove _daily_signal_fired + fix SessionProtection), then T-01, T-02, T-04, **T-05** (FAST/SLOW tier — MUST precede T-06/T-07), T-06, T-07, T-03, T-10 | 24h | — |
| 2 | **100-TRADE VALIDATION GATE (RK-01 + M-06)** | 0h code, 2-4 weeks paper | **WR >= 40% AND median ETS < 0.50** |
| 3 | SK-01, SK-02, SK-03 (SK-04 already done in step 1) | 5h | — |
| 4 | R21-19 (ISA gate) + RI-01 (IMAGE_PARITY) | 10h | — |
| 5 | R21-42 (VIX), R21-12 (Immutable), R21-16 (persist), R21-01 (SessionProtection) | 5h | — |
| 6 | AB-02 (timezone), R21-04 (iteration), R21-06 (queue), R21-13/14 (hysteresis), GQ-01 (IBKR reconnect loop), GQ-02 (Monday Go-NoGo) | 10h | — |
| 7 | R21-18 (weekly/monthly halts), RO-01, RO-02, RO-03 | 9h | — |
| 8 | **63-Day Paper Gauntlet** | 0h code, 63 days | Go/No-Go criteria |
| 9 | P1 items (non-deferred) | ~40h | — |
| 10 | **PHASE Q2**: Sections 2C-2F items (WebSocket, PostgreSQL, microstructure) | ~150h | Only after Q1 passes |

**Phase Q1 total**: ~63 hours of code + 100 trades + 63-day gauntlet.
**Phase Q2 total**: ~150 hours (microstructure infrastructure, only if Q1 validates the signal).

**The next line of text added to this document must be a git commit SHA of implemented code.**

---
