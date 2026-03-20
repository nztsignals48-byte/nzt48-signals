# AEGIS V2 -- QUESTION & DECISION REGISTER (QDR)

**System**: AEGIS V2 -- Autonomous Leveraged ETP Trading Engine (UK ISA)
**Audit Date**: 2026-03-20
**Audit Type**: ULTRATHINK Full-Spectrum Audit
**Auditor**: Claude Opus 4.6 (Anthropic)
**Codebase Snapshot**: ~30,000 LOC Rust + Python brain + 738 tests
**Deployment**: EC2 c7i-flex.large, Docker Compose (3 containers)

---

## Status Legend

| Status | Meaning |
|--------|---------|
| DECIDED | Question answered, decision locked, evidence verified in code |
| OPEN | Genuine unresolved item requiring human judgment or further data |
| DEFERRED | Deprioritised; will revisit when precondition is met |

---

## 1. ARCHITECTURE (Q-001 to Q-015)

### Q-001 | ARCHITECTURE | Why Rust+Python hybrid rather than pure Rust or pure Python?

**Decision**: Rust handles the latency-critical tick pipeline, risk arbiter, WAL persistence, and order lifecycle. Python handles signal generation (bridge.py) where library ecosystem (pandas, scipy, ta-lib) is critical for indicator math and rapid iteration.

**Evidence**: `rust_core/src/python_bridge.rs:1-7` documents the architecture rationale. Communication is JSON lines over stdin/stdout subprocess IPC, avoiding the PyO3 extension-module vs auto-initialize conflict.

**Status**: DECIDED

---

### Q-002 | ARCHITECTURE | How does the Rust engine communicate with the Python brain?

**Decision**: Subprocess IPC via JSON lines over stdin/stdout. The engine spawns `python3 /app/python_brain/bridge.py` and sends one JSON line per tick context, receiving one JSON line response (BrainSignal) synchronously.

**Evidence**: `rust_core/src/python_bridge.rs:163-200` -- PythonBridge struct holds Child process, stdin writer, BufReader on stdout. `start()` spawns the subprocess at line 178-186.

**Status**: DECIDED

---

### Q-003 | ARCHITECTURE | What is the tick processing pipeline order?

**Decision**: 8-step startup, then continuous tick loop: (1) Receive MarketTick from broker, (2) Route through Universe, (3) Update bar history/GARCH/Kalman, (4) Feed Python brain for signal, (5) Risk Arbiter evaluation (31 checks), (6) Position sizing via Kelly, (7) Order submission via Executioner, (8) Exit Engine evaluation on existing positions.

**Evidence**: `rust_core/src/engine.rs:1` describes the "8-step startup, tick processing, reconciliation, shutdown" architecture. Engine struct at line 305 holds all subsystems.

**Status**: DECIDED

---

### Q-004 | ARCHITECTURE | Is the system single-threaded or multi-threaded?

**Decision**: The core tick pipeline is single-threaded (synchronous RiskArbiter takes <1ms per evaluation, per `risk_arbiter.rs:110-111`). WAL writes run in tokio::task::spawn_blocking per `wal_writer.rs:2`. Supercronic runs Python cron jobs (nightly, config_writer) as separate processes.

**Evidence**: `rust_core/src/wal_writer.rs:2` -- "Runs in tokio::task::spawn_blocking (H13)". `rust_core/src/risk_arbiter.rs:110-111` -- "This is SYNCHRONOUS and takes < 1ms".

**Status**: DECIDED

---

### Q-005 | ARCHITECTURE | How many modules comprise the Rust core?

**Decision**: 48 public modules plus 10 test-only modules. This includes broker, engine, risk_arbiter, exit_engine, wal_writer, portfolio, position_sizer, entry_engine, python_bridge, config, config_loader, ibkr_broker, paper_broker, hardening, reconciler, universe, isa_gate, currency, garch_inference, garch_evt, cross_asset_macro, regime_detector, and 25+ more.

**Evidence**: `rust_core/src/lib.rs:1-86` enumerates all module declarations.

**Status**: DECIDED

---

### Q-006 | ARCHITECTURE | What is the role of the Universe module?

**Decision**: The Universe routes each incoming tick to the appropriate handler based on exchange/class (LSE Vanguard, Apex, Global) and filters tickers by subscription tier. It manages the 100-line IBKR subscription budget across potentially 303 contracts.

**Evidence**: `rust_core/src/engine.rs:52` imports Universe, UniverseClass, UniverseConfig. `config/config.toml:121-131` defines the 3-tier rotation scheme (50 permanent + 50 rotating lines).

**Status**: DECIDED

---

### Q-007 | ARCHITECTURE | How does the engine handle broker disconnection?

**Decision**: Multi-layered resilience: (1) BrokerHealthMonitor tracks connection quality, (2) CircuitBreaker trips after N errors in a window, (3) RiskArbiter CHECK 8 blocks all entries when broker_connected=false, escalating to HALT, (4) IB Gateway container has auto-restart and TWOFA_TIMEOUT_ACTION=restart.

**Evidence**: `rust_core/src/risk_arbiter.rs:161-164` -- CHECK 8 sets HALT on broker disconnect. `rust_core/src/hardening.rs:62-112` -- CircuitBreaker implementation. `docker-compose.yml:66` -- TWOFA_TIMEOUT_ACTION=restart.

**Status**: DECIDED

---

### Q-008 | ARCHITECTURE | What is the Engine generic type parameter for?

**Decision**: `Engine<B: BrokerAdapter>` is generic over the broker implementation. This allows PaperBroker for testing and IbkrBroker for live/paper IBKR connections. Both implement the BrokerAdapter trait.

**Evidence**: `rust_core/src/engine.rs:305` -- `pub struct Engine<B: BrokerAdapter>`. `rust_core/src/ibkr_broker.rs:1-6` documents IbkrBroker as the real adapter. `rust_core/src/paper_broker.rs` provides the test adapter.

**Status**: DECIDED

---

### Q-009 | ARCHITECTURE | What is the Crucible and what is its purpose?

**Decision**: The Crucible is the paper-trading validation phase (Days 1-63+). It uses relaxed risk parameters (15 max positions, 50% heat limit) to maximise trade data for Ouroboros learning, while keeping cost-model-critical parameters (spread veto, daily trade limit) at live-equivalent values.

**Evidence**: `config/config.toml:177-183` -- crucible section with max_positions_override=15, paper_mode=true, starting_equity_gbp=10000.

**Status**: DECIDED

---

### Q-010 | ARCHITECTURE | What latency profiling exists in the pipeline?

**Decision**: 6-stage pipeline latency profiler tracks tick-to-decision latency: (1) tick receive, (2) universe routing, (3) indicator computation, (4) Python brain IPC, (5) risk arbiter evaluation, (6) order submission.

**Evidence**: `rust_core/src/engine.rs:407` -- `pub latency_profiler: LatencyProfiler`. `rust_core/src/latency_profiler.rs` with PipelineStage enum imported at line 39.

**Status**: DECIDED

---

### Q-011 | ARCHITECTURE | How does config hot-reload work?

**Decision**: Config_writer runs at boot (entrypoint.sh) and is triggered via SIGHUP in the main loop. It regenerates dynamic_weights.toml from Ouroboros recommendations. The engine reloads these on SIGHUP signal. Watchlist files are hot-reloaded on a 15-minute cycle by checking file modification times.

**Evidence**: `rust_core/src/engine.rs:421-423` -- watchlist_mtimes and last_watchlist_rotation_ns fields. `config/dynamic_weights.toml:1-4` -- "auto-generated by config_writer".

**Status**: DECIDED

---

### Q-012 | ARCHITECTURE | Is there a dead letter queue for malformed signals?

**Decision**: Yes. The WAL writer has a dead_letter() method that writes unparseable OrderIntents to a separate dead-letter ndjson file per day, preventing data loss while keeping the main WAL clean.

**Evidence**: `rust_core/src/wal_writer.rs:124-134` -- dead_letter() method writes to dead_letter_dir with fsync.

**Status**: DECIDED

---

### Q-013 | ARCHITECTURE | How are the 303 contracts organized?

**Decision**: 49 LSEETF (primary ISA universe) + 70 US/SMART + 60 TSE + 40 HKEX + 39 KRX (broken -- account restriction) + 20 XETRA + 12 EURONEXT + 10 SGX + 3 other. Each contract has symbol, con_id, exchange, sec_type, currency, leverage, sector, and inverse_of fields.

**Evidence**: `config/contracts.toml:1-80` shows the contract schema and first entries. KRX contracts are documented as non-functional due to IBKR account-level restriction.

**Status**: DECIDED

---

### Q-014 | ARCHITECTURE | What is the simulation_mode flag and how does it differ from paper mode?

**Decision**: simulation_mode=true means trades are simulated internally (no orders submitted to IBKR at all). Paper mode (IS_LIVE=false) means orders go to IBKR's paper trading system. Current deployment uses both: IS_LIVE=false + simulation_mode=true, meaning IBKR provides market data only, all trades are internal simulations.

**Evidence**: `rust_core/src/engine.rs:429-434` -- simulation_mode field with documentation. `docker-compose.yml:5-6` -- "IS_LIVE=false, simulation_mode=true -- NEVER submits real orders".

**Status**: DECIDED

---

### Q-015 | ARCHITECTURE | What is the role of Redis in the architecture?

**Decision**: Redis (7-alpine, password-protected) serves as state cache for cross-process data sharing: Ouroboros recommendations, circuit breaker state, and potentially Chandelier rung persistence. Configured with appendonly=yes, appendfsync=everysec, maxmemory=256mb with noeviction policy.

**Evidence**: `docker-compose.yml:89-117` -- Redis service configuration. Password is `nzt48redis`, not exposed on host port (internal Docker network only).

**Status**: DECIDED

---

## 2. RISK MANAGEMENT (Q-016 to Q-030)

### Q-016 | RISK_MANAGEMENT | How many risk checks does the RiskArbiter perform?

**Decision**: 31 checks in deterministic order, fail-closed. Numbered CHECK 1 through CHECK 29 are visible in code, plus ISA short-sell block, inverse mutual exclusion, and regime-level gates. Any single check failure results in immediate rejection.

**Evidence**: `rust_core/src/risk_arbiter.rs:1-2` -- "31-check risk gate with 4-state regime hierarchy. HALT > FLATTEN > REDUCE > NORMAL. Fail-closed."

**Status**: DECIDED

---

### Q-017 | RISK_MANAGEMENT | What are the 4 risk regime states?

**Decision**: Normal (full operation) < Reduce (reduced sizing) < Flatten (close all, block entries) < Halt (emergency stop, manual clear required). The hierarchy is strictly ordered: escalation is automatic, de-escalation requires specific conditions or manual intervention.

**Evidence**: `rust_core/src/risk_arbiter.rs:412-438` -- escalate(), clear_reduce(), clear_flatten(), manual_clear_halt() methods. Only HALT requires human approval to clear.

**Status**: DECIDED

---

### Q-018 | RISK_MANAGEMENT | Why is short selling blocked at the risk arbiter level?

**Decision**: UK ISA rules prohibit short selling. CHECK 1 in the RiskArbiter immediately rejects any Short direction order and escalates to HALT as a protective measure. Inverse ETPs (QQQS.L, 3USS.L) provide synthetic short exposure within ISA rules.

**Evidence**: `rust_core/src/risk_arbiter.rs:122-126` -- "CHECK 1: ISA Safety -- side == Short -> HALT + REJECT (P0)".

**Status**: DECIDED

---

### Q-019 | RISK_MANAGEMENT | What is the daily drawdown limit and what happens when breached?

**Decision**: 4.0% daily drawdown limit (raised from 2.0% per audit, as single 3x ETP stop can hit 3%). Breach triggers FLATTEN regime -- all positions closed, no new entries. Skipped in simulation mode to avoid halting data collection.

**Evidence**: `config/config.toml:65` -- daily_drawdown_pct = 4.0. `rust_core/src/risk_arbiter.rs:275-278` -- CHECK 18 sets FLATTEN on breach.

**Status**: DECIDED

---

### Q-020 | RISK_MANAGEMENT | How does the spread veto work?

**Decision**: CHECK 13 computes spread_pct = (ask - bid) / bid * 100.0. If spread exceeds 0.3% (config spread_veto_pct), the entry is rejected. This is ALWAYS enforced, including in simulation mode, to ensure paper trade data reflects realistic execution costs.

**Evidence**: `rust_core/src/risk_arbiter.rs:193-203` -- CHECK 13 implementation. `config/config.toml:72` -- spread_veto_pct = 0.3.

**Status**: DECIDED

---

### Q-021 | RISK_MANAGEMENT | What is the maximum daily trade limit and why?

**Decision**: 3 trades per day (N0a). At 0.50% round-trip cost per trade, each trade costs approximately 10 GBP on a 2K GBP position. 3 trades/day x 252 days = 7,560 GBP/year = 76% equity drag on 10K GBP portfolio. This is the #1 survival lever.

**Evidence**: `config/config.toml:78` -- max_daily_trades = 3. `rust_core/src/risk_arbiter.rs:205-217` -- CHECK 28 enforced in ALL modes including simulation.

**Status**: DECIDED

---

### Q-022 | RISK_MANAGEMENT | How does GARCH-based volatility gating work?

**Decision**: CHECK 25 rejects entries when GARCH forecast sigma exceeds a leverage-scaled threshold: 0.80 * sqrt(leverage_factor). For 3x ETPs, threshold = 0.80 * 1.73 = 1.39 annualized sigma. For 5x ETPs, threshold = 0.80 * 2.24 = 1.79.

**Evidence**: `rust_core/src/risk_arbiter.rs:336-345` -- CHECK 25 with Avellaneda & Zhang 2010 scaling.

**Status**: DECIDED

---

### Q-023 | RISK_MANAGEMENT | What is the confidence floor and how was it determined?

**Decision**: 65 (N0c). Raised from 45 because at 45%, signals are barely above coin-flip and waste the cost budget. The floor is enforced in RiskArbiter CHECK 10 and is not relaxed in simulation mode.

**Evidence**: `config/config.toml:7` -- confidence_floor = 65. `rust_core/src/risk_arbiter.rs:173-180` -- CHECK 10.

**Status**: DECIDED

---

### Q-024 | RISK_MANAGEMENT | How does the minimum gross edge gate (N0d) work?

**Decision**: CHECK 29 rejects trades where spread alone exceeds 2x the min_gross_edge_pct threshold (0.15%). If spread_pct > 0.30%, the trade is rejected as the expected edge cannot cover transaction costs. This prevents "spread victim" trades (L5 classification).

**Evidence**: `rust_core/src/risk_arbiter.rs:219-235` -- CHECK 29. `config/config.toml:81` -- min_gross_edge_pct = 0.15.

**Status**: DECIDED

---

### Q-025 | RISK_MANAGEMENT | How does the macro regime (VIX/DXY/credit) affect trading?

**Decision**: CrossAssetMacro classifies regime as Normal/Caution/Stress/Crisis. Crisis (VIX spike + wide credit) triggers FLATTEN. Stress + stale ticks triggers HALT. Stale macro data with non-Normal signal triggers REDUCE. VIX tiers define allocation caps: <18 full, 18-25 50%, 25-35 25%, 35-50 inverse only, >50 full halt.

**Evidence**: `rust_core/src/risk_arbiter.rs:458-492` -- evaluate_macro_escalation(). `config/config.toml:86-91` -- VIX tier configuration.

**Status**: DECIDED

---

### Q-026 | RISK_MANAGEMENT | What prevents the portfolio from being overconcentrated?

**Decision**: Multiple layers: (1) Sector heat cap at 33% live / 80% paper, (2) Inverse mutual exclusion (cannot hold QQQ3.L and QQQS.L simultaneously), (3) Max correlated positions = 3 for instruments with correlation > 0.7, (4) CVaR heat check at 1.5x the basic heat limit.

**Evidence**: `config/config.toml:21` -- sector_heat_cap_pct. `config/config.toml:70` -- max_correlated_positions = 3. `rust_core/src/risk_arbiter.rs:323-333` -- CHECK 24 CVaR heat.

**Status**: DECIDED

---

### Q-027 | RISK_MANAGEMENT | What is the consecutive loss breaker?

**Decision**: After 5 consecutive stop losses (raised from 3 per audit), the RiskArbiter escalates to HALT. Audit found 3 was too aggressive -- triggers in 1 out of 6 random sequences at 45% WR. The 5-loss threshold provides more statistical significance.

**Evidence**: `config/config.toml:74` -- consecutive_loss_halt = 5. `rust_core/src/risk_arbiter.rs:296-300` -- CHECK 21.

**Status**: DECIDED

---

### Q-028 | RISK_MANAGEMENT | How does position sizing account for Kelly criterion limitations?

**Decision**: 12-factor Kelly sizing with multiple caps: (1) Raw Kelly clamped to [0.0, 1.0], (2) Fractional Kelly at 25% (position_sizer.rs:11), (3) Half-Kelly hard cap at 0.20 (config kelly.clamp_max), (4) Ouroboros per-ticker Kelly caps, (5) Kelly ramp scaling for insufficient data, (6) Regime-based scaling multiplier.

**Evidence**: `rust_core/src/position_sizer.rs:10-61` -- KellyCalculator. `config/config.toml:27-28` -- fraction_cap=0.5, clamp_max=0.20. `rust_core/src/risk_arbiter.rs:370-378` -- regime scaling.

**Status**: DECIDED

---

### Q-029 | RISK_MANAGEMENT | Is there a peak drawdown halt (trailing from high-water mark)?

**Decision**: Yes. peak_drawdown_halt_pct = 15.0 triggers full halt when equity drops 15% from HWM. Additionally, equity_floor_pct = 70.0 provides a hard floor at 70% of initial equity (7,000 GBP). Weekly drawdown limit is 7.0%.

**Evidence**: `config/config.toml:67-68` -- peak_drawdown_halt_pct = 15.0, equity_floor_pct = 70.0.

**Status**: DECIDED

---

### Q-030 | RISK_MANAGEMENT | Are risk parameters relaxed in simulation mode and does this invalidate paper results?

**Decision**: Cost-critical parameters (spread veto, daily trade limit, max positions, confidence floor) are enforced in ALL modes. Cash buffer, portfolio heat, sector heat, ISA limit, and drawdown breaker ARE relaxed in simulation. This is intentional: the relaxed parameters allow broader data collection while the enforced parameters ensure cost-representative economics.

**Evidence**: `rust_core/src/risk_arbiter.rs:240-278` -- Comments explain which checks skip simulation_mode and why. `rust_core/src/engine.rs:475-490` -- Paper mode calibration comments.

**Status**: DECIDED

---

## 3. SIGNAL GENERATION (Q-031 to Q-045)

### Q-031 | SIGNAL_GENERATION | What strategies are currently active?

**Decision**: Two primary strategies per audit consensus: S20 Cross-Market Momentum (priority 1) and S17 VWAP DipBuy (priority 2). S18 Gap Fade and S21 Intraday Momentum are disabled. S19 RSI/IBS is enabled but low priority. VanguardSniper in bridge.py is the primary live signal generator.

**Evidence**: `config/strategies.toml:25-33` -- max_active_strategies = 2, priority_order defined. `config/strategies.toml:103-105` -- gap_fade enabled=true but priority=1 (contradiction with global disabled status).

**Status**: OPEN -- Gap Fade shows enabled=true in strategies.toml but is documented as disabled per audit. Config inconsistency needs resolution.

---

### Q-032 | SIGNAL_GENERATION | What are the 4 entry types in the Rust entry engine?

**Decision**: Type A (DipRecovery, 65% base confidence), Type B (EarlyRunner, 82% base confidence -- documented as "YOUR EDGE"), Type C (OverboughtFade, 72% base confidence -- inverse ETP shorting), Type D (SupportBounce, 70% base confidence). All route through VanguardSniper strategy.

**Evidence**: `rust_core/src/entry_engine.rs:14-23` -- EntryType enum. `rust_core/src/entry_engine.rs:74-97` -- DipRecoveryDetector implementation.

**Status**: DECIDED

---

### Q-033 | SIGNAL_GENERATION | How does the Python brain return signals?

**Decision**: BrainSignal struct contains: direction (Long/Short string), confidence (0-100), kelly_fraction (0-0.20), shares (quantity), strategy (name), plus indicator context (rvol, hurst, adx) for Ouroboros learning.

**Evidence**: `rust_core/src/python_bridge.rs:17-38` -- BrainSignal struct with PyO3 annotations.

**Status**: DECIDED

---

### Q-034 | SIGNAL_GENERATION | What indicator context is provided to the Python brain per tick?

**Decision**: TickContext includes 14 fields: win_rate, total_trades, avg_win, avg_loss, leverage, realized_vol, correlation, drawdown_pct, amihud (illiquidity), regime (4-state), spread_pct, time_fraction, heat_pct, and equity.

**Evidence**: `rust_core/src/python_bridge.rs:69-100` -- TickContext struct definition.

**Status**: DECIDED

---

### Q-035 | SIGNAL_GENERATION | How is NaN/Infinity prevented from entering the system?

**Decision**: validate_f64() helper rejects NaN and Infinity with descriptive error messages. Applied at the PyO3 boundary on every f64 field in OrderIntent construction. Confidence is clamped to [0.0, 100.0], Kelly fraction to [0.0, 0.20].

**Evidence**: `rust_core/src/types/structs.rs:77-85` -- validate_f64(). Lines 124-132 -- NaN sanitization on OrderIntent construction.

**Status**: DECIDED

---

### Q-036 | SIGNAL_GENERATION | What is the Structural Tradability Score (N3a)?

**Decision**: 5-component composite score (0-100) computed in bridge.py that evaluates a ticker's current tradability based on spread quality, volume profile, volatility regime, recent performance, and session fit. Used as a pre-filter before signal generation.

**Evidence**: Documented as N3a build item. `rust_core/src/risk_arbiter.rs:347-355` -- CHECK 26 rejects scanner scores below 30.

**Status**: DECIDED

---

### Q-037 | SIGNAL_GENERATION | How does regime detection work?

**Decision**: Two-layer regime detection: (1) Jump-diffusion detector (RVOL threshold 3.5x + price move > 2x ATR) blocks entries during flash crashes, (2) Hurst exponent estimation classifies trending (H > 0.5), mean-reverting (H < 0.5), or random walk (H ~ 0.5) regimes. Strategies are filtered by regime eligibility.

**Evidence**: `rust_core/src/regime_detector.rs:1-50` -- JumpDiffusionDetector. `config/strategies.toml:77-78` -- regime_eligible per strategy.

**Status**: DECIDED

---

### Q-038 | SIGNAL_GENERATION | How does the data staleness check protect signal quality?

**Decision**: CHECK 7 in RiskArbiter rejects entries and escalates to HALT if the last tick for a ticker is older than 120 seconds. This prevents trading on stale quotes that could lead to phantom fills or incorrect stop placement.

**Evidence**: `config/config.toml:36` -- stale_data_threshold_secs = 120. `rust_core/src/risk_arbiter.rs:149-158` -- CHECK 7.

**Status**: DECIDED

---

### Q-039 | SIGNAL_GENERATION | What is the velocity check and what does it prevent?

**Decision**: CHECK 19 limits the number of order intents per ticker within a time window (1 second, max 5 intents). This prevents signal cascade/feedback loops where a single ticker generates dozens of intents per second.

**Evidence**: `config/config.toml:11-12` -- velocity_check_window_secs = 1, velocity_check_max_intents = 5. `rust_core/src/risk_arbiter.rs:280-289` -- CHECK 19.

**Status**: DECIDED

---

### Q-040 | SIGNAL_GENERATION | How does the ticker blacklist work?

**Decision**: Ouroboros nightly identifies tickers with WR < 30% over 10+ trades and writes them to dynamic_weights.toml [ticker_blacklist]. The engine loads this at boot and on SIGHUP. The RiskArbiter holds the blacklist and bridge.py rejects signals for blacklisted tickers as a pre-filter.

**Evidence**: `rust_core/src/ouroboros_loader.rs:22-24` -- ticker_blacklist field. `rust_core/src/risk_arbiter.rs:93` -- pub ticker_blacklist: Vec<String>. `config/dynamic_weights.toml:37-39` -- currently empty.

**Status**: DECIDED

---

### Q-041 | SIGNAL_GENERATION | What is the PredictiveScorer and how does it gate re-entry?

**Decision**: Tracks per-ticker Information Coefficient (IC) and trade count. After 5 consecutive losses, a ticker is "locked" (ticker_locked=true). Re-entry gating in CHECK 22: locked tickers get max 1 position, tickers with IC >= 0.20 and 20+ trades get up to 3, IC >= 0.10 and 10+ trades get up to 2, default is 1.

**Evidence**: `rust_core/src/risk_arbiter.rs:302-316` -- CHECK 22 momentum re-entry logic. `rust_core/src/engine.rs:387` -- predictive_scorer field.

**Status**: DECIDED

---

### Q-042 | SIGNAL_GENERATION | Is there a cooldown between signals for the same ticker?

**Decision**: Yes, per-ticker 25-minute cooldown enforced in bridge.py. Additionally, gap_cooldown_mins = 15 in config prevents immediate re-entry after gap events. The velocity check (CHECK 19) provides sub-second burst protection.

**Evidence**: `config/config.toml:48` -- gap_cooldown_mins = 15. Velocity check at `risk_arbiter.rs:280-289`.

**Status**: DECIDED

---

### Q-043 | SIGNAL_GENERATION | How does the VWAP DipBuy strategy determine entry?

**Decision**: Entry at 2 sigma below intraday VWAP with declining volume (noise dip, not real breakdown). VWAP slope must be flat (< 0.01). Requires ADX < 25 (no strong trend), Hurst < 0.50 (mean-reverting), VIX < 30. Eligible sessions: 10:30-14:30 and 14:30-16:00 London time.

**Evidence**: `config/strategies.toml:42-94` -- Complete S17 VWAP DipBuy specification.

**Status**: DECIDED

---

### Q-044 | SIGNAL_GENERATION | How does the Cross-Market Momentum strategy work?

**Decision**: Monitors S&P 500 direction in first 15 minutes of US open. If SPY moves > 0.3% in a direction, enters LSE ETP in the same direction. Requires ADX > 20, RVOL > 1.2, Hurst > 0.50 (trending). Session: 14:45-16:00 London (US overlap). 90-minute time stop with EOD flatten.

**Evidence**: `config/strategies.toml:218-258` -- Complete S20 specification.

**Status**: DECIDED

---

### Q-045 | SIGNAL_GENERATION | What happens when the Python brain crashes or returns errors?

**Decision**: PythonBridge tracks consecutive_errors (line 173). Persistent errors from the Python subprocess indicate strategy crash. The engine logs stderr from the child process (Stdio::inherit at line 183). There is no documented automatic restart of the Python subprocess.

**Evidence**: `rust_core/src/python_bridge.rs:173` -- consecutive_errors field. Line 183 -- stderr: Stdio::inherit().

**Status**: OPEN -- No automatic Python subprocess restart mechanism is documented. If bridge.py crashes, signal generation stops until engine restart.

---

## 4. ORDER LIFECYCLE (Q-046 to Q-055)

### Q-046 | ORDER_LIFECYCLE | What is the order flow from signal to fill?

**Decision**: Python SUGGESTS (OrderIntent) -> Rust DECIDES (RiskArbiter) -> Executioner submits -> Broker ACK -> Fill event -> WAL write -> Portfolio update. The principle is "Python has no gun" -- it can only suggest, never directly submit orders.

**Evidence**: `rust_core/src/types/structs.rs:87-88` -- "Python SUGGESTS. Rust DECIDES. Python has no gun (Non-Negotiable #2)".

**Status**: DECIDED

---

### Q-047 | ORDER_LIFECYCLE | How are orders submitted to IBKR?

**Decision**: IbkrBroker (client_id=101) connects to IB Gateway on port 4003 via the ibapi crate. Marketable limit orders with a 0.1% buffer (config execution.marketable_limit_buffer_pct) are used rather than market orders, reducing slippage risk.

**Evidence**: `config/config.toml:94` -- marketable_limit_buffer_pct = 0.1. `rust_core/src/ibkr_broker.rs:1-6` -- Connection details documented.

**Status**: DECIDED

---

### Q-048 | ORDER_LIFECYCLE | How does the Chandelier Exit determine stop prices?

**Decision**: 5-rung trailing stop: Rung 1 (entry, stop = entry - 1.5x ATR), Rung 2 (+0.8%, stop = breakeven + fees), Rung 3 (+1.5%, trail 1.0x ATR), Rung 4 (+2.5%, trail 0.75x ATR), Rung 5 (+4.0%, trail 0.5x ATR). No partial sells -- all exits are 100% of position. Rung thresholds were tightened per audit for compounding optimality.

**Evidence**: `rust_core/src/exit_engine.rs:40-76` -- ChandelierStrategy implementation with rung_pct_thresholds: [0.0, 0.008, 0.015, 0.025, 0.040].

**Status**: DECIDED

---

### Q-049 | ORDER_LIFECYCLE | What is the EOD flatten protocol?

**Decision**: 3-phase flatten: Phase 1 (T-35, 15:55) passive limit at mid+1tick, Phase 2 (T-15, 16:15) limit at mid, Phase 3 (T-5, 16:25) MTL emergency. All positions must be flat before LSE closing auction at 16:30.

**Evidence**: `config/config.toml:44-47` -- eod_flatten_time, phase1/phase2/phase3 times.

**Status**: DECIDED

---

### Q-050 | ORDER_LIFECYCLE | How is the entry cutoff enforced?

**Decision**: CHECK 11 in RiskArbiter rejects all new entries after 15:45 London time (entry_cutoff_london in config). This ensures no new positions are opened within 45 minutes of market close, providing time for the EOD flatten protocol. Skipped in simulation mode.

**Evidence**: `config/config.toml:37` -- entry_cutoff_london = "15:45". `rust_core/src/risk_arbiter.rs:183-186` -- CHECK 11.

**Status**: DECIDED

---

### Q-051 | ORDER_LIFECYCLE | What is the round-trip cost assumption?

**Decision**: 0.3% round-trip fee (entry commission + exit commission + spread cost). Used in Chandelier Rung 2 breakeven calculation. The config also has slippage_assumption_pct = 0.5% separately.

**Evidence**: `rust_core/src/exit_engine.rs:73` -- round_trip_fee_pct: 0.003. `config/config.toml:73` -- slippage_assumption_pct = 0.5.

**Status**: OPEN -- The exit engine uses 0.3% round-trip but config assumes 0.5% slippage on top. These should be reconciled to determine the true all-in cost model used for edge calculations.

---

### Q-052 | ORDER_LIFECYCLE | How does reconciliation work?

**Decision**: Every 5 minutes (300 seconds), the reconciler compares local PortfolioState against broker-reported positions. Mismatches (quantity diff, cost diff, broker-only, local-only) trigger CRITICAL log + FLATTEN. A 24-hour audit lock period prevents automatic regime reset -- only manual_clear_halt can resume.

**Evidence**: `rust_core/src/reconciler.rs:1-100` -- Full reconciliation implementation. `config/config.toml:109` -- interval_secs = 300.

**Status**: DECIDED

---

### Q-053 | ORDER_LIFECYCLE | What is the dust threshold?

**Decision**: SC-06 defines a dust_threshold_gbp in the ExitConfig. Remainder positions below this threshold are closed with a market sell to prevent tiny orphaned positions that cost more to exit than they are worth.

**Evidence**: `rust_core/src/exit_engine.rs:142-143` -- "SC-06: Dust threshold in GBP. Remainder below this -> market sell."

**Status**: DECIDED

---

### Q-054 | ORDER_LIFECYCLE | How does the tick accumulator throttle data?

**Decision**: TickAccumulator in IbkrBroker builds synthetic bars from reqMktData ticks and throttles emission to every 5 seconds. This prevents backpressure from high-frequency tick updates overwhelming the pipeline on liquid instruments.

**Evidence**: `rust_core/src/ibkr_broker.rs:77-89` -- TickAccumulator struct with 5s cadence throttle.

**Status**: DECIDED

---

### Q-055 | ORDER_LIFECYCLE | What happens to open positions during an engine restart?

**Decision**: WAL replay on startup reconstructs all position state from event history. The WAL reader scans current + archive/*.ndjson files. RungAdvanced events restore Chandelier rung state. After replay, a reconciliation against broker positions verifies consistency before entering Normal mode.

**Evidence**: `rust_core/src/engine.rs:86-94` -- StartupResult includes wal_events_replayed, positions_reconciled. WAL archive fix documented in project memory.

**Status**: DECIDED

---

## 5. WAL PERSISTENCE (Q-056 to Q-065)

### Q-056 | WAL_PERSISTENCE | What is the WAL event format?

**Decision**: Append-only ndjson (newline-delimited JSON). Each event has: event_id (UUIDv7), schema_version (1), event_time_ns, write_time_ns, checksum (CRC32 of payload), and payload (one of 21+ event types).

**Evidence**: `rust_core/src/wal_writer.rs:147-155` -- make_wal_event() builds events with UUIDv7. Lines 89-91 -- CRC32 computation over payload JSON.

**Status**: DECIDED

---

### Q-057 | WAL_PERSISTENCE | How does the WAL ensure data integrity?

**Decision**: Triple protection: (1) CRC32 checksum computed over payload JSON before write, (2) flush() + set_len() truncation to prevent EOF corruption from partial writes (WP-1), (3) sync_all() fsync after every write to ensure data reaches stable storage (WP-3).

**Evidence**: `rust_core/src/wal_writer.rs:80-121` -- append() method with all three protections.

**Status**: DECIDED

---

### Q-058 | WAL_PERSISTENCE | What are the WAL event types?

**Decision**: 21+ event types including: OrderIntent, OrderAck, FillEvent, PositionOpened, PositionClosed (enriched with N2b fields), RungAdvanced, ExitTriggered, RegimeChange, DailyReset, Heartbeat, Reconcile, SignalRejected (N2a), MissedWinnerCandidate (N2c), and more.

**Evidence**: `rust_core/src/types/mod.rs` -- WalPayload enum. N2a/N2b/N2c documented as recent build items.

**Status**: DECIDED

---

### Q-059 | WAL_PERSISTENCE | How does WAL file rotation work?

**Decision**: One WAL file per trading day (YYYY-MM-DD.ndjson). On engine restart, old files are moved to archive/ directory. WalCompressor handles rotation at 1M event threshold. read_all_wal_files() scans current + archive/*.ndjson for replay.

**Evidence**: `rust_core/src/wal_writer.rs:136-138` -- today_path() generates date-based filename. `rust_core/src/engine.rs:397` -- wal_compressor: WalCompressor field.

**Status**: DECIDED

---

### Q-060 | WAL_PERSISTENCE | What happens if disk space runs low?

**Decision**: WAL writer checks disk space before every append via an injectable disk_check_fn. If free space drops below 5%, the write fails with WalError::DiskSpaceLow. RiskArbiter CHECK 9 then blocks all entries because wal_available=false, escalating to HALT.

**Evidence**: `rust_core/src/wal_writer.rs:81-86` -- Disk space check (H25). Lines 16-17 -- WalError::DiskSpaceLow variant.

**Status**: DECIDED

---

### Q-061 | WAL_PERSISTENCE | What enriched fields does PositionClosed contain (N2b)?

**Decision**: hold_time_mins, entry_session_phase, gross_pnl, total_commission, spread_at_entry_pct, spread_at_exit_pct, mae (Maximum Adverse Excursion), mfe (Maximum Favorable Excursion), rung_achieved, and indicator context (rvol, hurst, adx at entry).

**Evidence**: `python_brain/ouroboros/nightly_v6.py:82-100` -- TradeRecord dataclass mirrors the enriched WAL fields.

**Status**: DECIDED

---

### Q-062 | WAL_PERSISTENCE | How is the WAL replay tested?

**Decision**: Dedicated test modules: replay_tests.rs and wal_tests.rs. Tests verify event round-trip (serialize -> write -> read -> deserialize), CRC32 validation on corrupt data, position reconstruction from event sequence, and rung state restoration.

**Evidence**: `rust_core/src/lib.rs:64` -- mod replay_tests. `rust_core/src/lib.rs:95-96` (continuing) -- mod wal_tests.

**Status**: DECIDED

---

### Q-063 | WAL_PERSISTENCE | What is the dead letter queue for?

**Decision**: Unparseable OrderIntents from the Python brain are written to a separate dead letter ndjson file (one per day in dead_letter_dir). This preserves evidence of malformed signals for debugging without corrupting the main WAL.

**Evidence**: `rust_core/src/wal_writer.rs:124-134` -- dead_letter() method.

**Status**: DECIDED

---

### Q-064 | WAL_PERSISTENCE | What is the WAL schema version and migration plan?

**Decision**: Schema version = 1. Stored in every WAL event (schema_version field). Forward-compatible: readers should skip events with unknown schema versions. No migration tooling exists yet.

**Evidence**: `config/config.toml:134` -- schema_version = 1. `rust_core/src/wal_writer.rs:150-155` -- schema_version: 1 in make_wal_event.

**Status**: OPEN -- No WAL schema migration tooling exists. When schema_version increments, how will old WAL files be handled? Need a migration plan before schema changes.

---

### Q-065 | WAL_PERSISTENCE | Is there WAL compression?

**Decision**: WalCompressor handles rotation at 1M event threshold. State checkpoints (hourly, FNV-1a hash) allow the engine to skip replaying the full WAL and resume from a known-good checkpoint. This bounds replay time as WAL grows.

**Evidence**: `rust_core/src/engine.rs:397-399` -- wal_compressor and checkpoint_mgr fields.

**Status**: DECIDED

---

## 6. OUROBOROS (Q-066 to Q-075)

### Q-066 | OUROBOROS | What is the Ouroboros nightly pipeline?

**Decision**: Runs at 04:50 UTC every weekday (23:50 ET). 6 steps: (1) Trade analysis from WAL, (2) Regime accuracy check, (3) Parameter optimization with guardrails, (4) Alpha decay detection (7d vs 30d rolling), (5) Daily report generation, (6) Pre-market battle plan.

**Evidence**: `python_brain/ouroboros/nightly_v6.py:1-17` -- Module docstring with full pipeline description.

**Status**: DECIDED

---

### Q-067 | OUROBOROS | What guardrails prevent Ouroboros from destroying the system?

**Decision**: Hard limits: Kelly [0.15, 0.30], Chandelier ATR mult [1.5, 4.0], max 15% drift per parameter per night. Quarantine rules: NEVER writes to live WAL, NEVER influences live decisions in-session, reads ONLY the finished day's journal.

**Evidence**: `python_brain/ouroboros/nightly_v6.py:58-63` -- KELLY_MIN=0.15, KELLY_MAX=0.30, CHANDELIER_ATR_MIN=1.5, CHANDELIER_ATR_MAX=4.0, MAX_DRIFT_PCT=15.0.

**Status**: DECIDED

---

### Q-068 | OUROBOROS | What is the current Bayesian win rate?

**Decision**: 79.17% over 20 trades (as of 2026-03-19). However, Sharpe ratio is 0.0 and DSR is 0.0 (not statistically significant), indicating insufficient data for reliable inference. The regime scaling shows Normal = 1.60 (boosted), which may be over-optimistic with only 20 trades.

**Evidence**: `config/dynamic_weights.toml:8-13` -- bayesian section with exact values.

**Status**: OPEN -- 20 trades is too few for reliable Bayesian estimates. Normal regime scale at 1.60 risks over-sizing. Should revert to 1.0 until trade count reaches 50+.

---

### Q-069 | OUROBOROS | How does the dynamic weights file get loaded?

**Decision**: OuroborosLoader reads dynamic_weights.toml at engine boot. Parses Bayesian stats, exit parameters (chandelier ATR mult), regime scales, per-ticker Kelly fractions, and ticker blacklist. If loading fails, safe defaults are used (yesterday's values).

**Evidence**: `rust_core/src/ouroboros_loader.rs:1-100` -- Full loader implementation with fallback defaults.

**Status**: DECIDED

---

### Q-070 | OUROBOROS | What is alpha decay detection?

**Decision**: Compares 7-day rolling metrics against 30-day rolling metrics. If recent performance significantly degrades relative to the longer window, it signals alpha decay -- the strategy's edge may be eroding. This triggers more conservative parameter adjustments.

**Evidence**: `python_brain/ouroboros/nightly_v6.py:4` -- "Alpha decay detection (7d vs 30d rolling)" in pipeline step 4.

**Status**: DECIDED

---

### Q-071 | OUROBOROS | How does the trade taxonomy (N1b) classify trades?

**Decision**: 14-class classifier in trade_taxonomy.py categorizes each closed trade by outcome pattern (e.g., Spread Victim, Momentum Winner, False Signal, etc.). This enables targeted parameter adjustment -- e.g., if 40% of losses are Spread Victims, tighten the spread veto.

**Evidence**: Documented as N1b build item. `python_brain/ouroboros/nightly_v6.py:94-100` shows cost-aware fields (gross_pnl, commission, spread_at_entry/exit) feeding taxonomy.

**Status**: DECIDED

---

### Q-072 | OUROBOROS | What is the cost-aware nightly learning (N1a)?

**Decision**: Ouroboros now factors in gross PnL, total commission, and spread costs when evaluating trade performance. Previous versions used net PnL only, which masked whether wins were genuine edge or just favorable spread conditions. This prevents optimizing for scenarios that cannot be profitably replicated.

**Evidence**: `python_brain/ouroboros/nightly_v6.py:95-100` -- TradeRecord includes gross_pnl, total_commission, spread_at_entry_pct, spread_at_exit_pct.

**Status**: DECIDED

---

### Q-073 | OUROBOROS | Can Ouroboros modify the confidence floor?

**Decision**: The dynamic_weights.toml [signal] section includes confidence_floor = 45, but the static config.toml sets it to 65. The engine loads static config first, then overlays dynamic weights. It is unclear whether the dynamic confidence_floor of 45 overrides the static 65.

**Evidence**: `config/dynamic_weights.toml:34-35` -- confidence_floor = 45. `config/config.toml:7` -- confidence_floor = 65.

**Status**: OPEN -- If Ouroboros can lower confidence_floor from 65 to 45, it undermines the N0c survival gate. This should be guarded: dynamic_weights should only be able to RAISE the floor, never lower it below the static minimum.

---

### Q-074 | OUROBOROS | How are indicator gates discovered and applied?

**Decision**: Config_writer generates [indicator_gates] in dynamic_weights.toml based on persistent memory analysis. Bridge.py reads these as pre-signal filters. Gate vetoes are logged to /app/data/gate_vetoes.ndjson with full indicator context for missed-winner analysis.

**Evidence**: `config/dynamic_weights.toml:41-42` -- [indicator_gates] section (currently empty). Gate veto logging documented in project memory.

**Status**: DECIDED

---

### Q-075 | OUROBOROS | What is the Ouroboros output cadence?

**Decision**: Nightly at 04:50 UTC: full analysis + dynamic_weights.toml update. Config_writer at 04:51 UTC: regenerates config files. Ticker_selector every 15 minutes: re-ranks tradeable universe. Session PDFs at session opens. All via Supercronic crontab.

**Evidence**: `Dockerfile:40` -- "Ouroboros crontab: 23:50 ET = 04:50 UTC". Project memory: "Supercronic runs crontab: nightly_v6 04:50 UTC, config_writer 04:51 UTC, ticker_selector every 15min".

**Status**: DECIDED

---

## 7. DEPLOYMENT (Q-076 to Q-085)

### Q-076 | DEPLOYMENT | What is the production infrastructure?

**Decision**: EC2 c7i-flex.large (4GB RAM, 2 vCPUs, x86_64) in us-east-1c with Elastic IP 3.230.44.22. Docker Compose with 3 containers: aegis-v2 (engine, 1024M limit), aegis-ib-gateway (IB Gateway, 1024M limit), aegis-redis (256M limit + 512M container limit). Total memory budget: 2.5GB of 4GB available.

**Evidence**: `docker-compose.yml:1-127` -- Complete deployment specification.

**Status**: DECIDED

---

### Q-077 | DEPLOYMENT | How does the Docker build work?

**Decision**: Multi-stage Dockerfile based on python:3.12-bookworm. Installs Rust toolchain, Supercronic, Python deps, then builds: (1) cargo build --release --bin aegis, (2) maturin builds PyO3 extension wheel, (3) pip installs the wheel. Final image contains both Rust binary and Python runtime. TZ=UTC to prevent cron schedule drift.

**Evidence**: `Dockerfile:1-61` -- Complete build specification. Line 50 -- TZ=UTC with audit note about previous 4-5h drift.

**Status**: DECIDED

---

### Q-078 | DEPLOYMENT | What is the IB Gateway configuration?

**Decision**: gnzsnz/ib-gateway:stable image. TRADING_MODE=live (for real market data), READ_ONLY_API=false, TWOFA_TIMEOUT_ACTION=restart. Exposes ports 4001 and 4003. Health check every 30s via TCP probe on 4003. Weekly 2FA re-auth required Monday mornings.

**Evidence**: `docker-compose.yml:58-87` -- IB Gateway service configuration.

**Status**: DECIDED

---

### Q-079 | DEPLOYMENT | How is data persisted across container restarts?

**Decision**: Three Docker named volumes: aegis-events (WAL files), aegis-data (reports, recommendations, metrics), aegis-redis-data (Redis AOF). Config is bind-mounted from host ./config. This ensures WAL and state survive container rebuilds.

**Evidence**: `docker-compose.yml:33-35` -- Volume mounts. Lines 119-122 -- Volume declarations.

**Status**: DECIDED

---

### Q-080 | DEPLOYMENT | What is the deployment workflow?

**Decision**: git add -> git commit -> git push -> rsync to EC2 -> docker compose build -> docker compose up -d. Local, GitHub, and EC2 must ALWAYS be in sync. Docker image bakes Python -- scp alone does not work.

**Evidence**: Project memory DEPLOYMENT RULE. `Dockerfile:33` -- cargo build --release inside Docker.

**Status**: DECIDED

---

### Q-081 | DEPLOYMENT | How is the engine health monitored?

**Decision**: Docker HEALTHCHECK every 30s checks if aegis process is running (pgrep). Engine emits 5-minute heartbeats internally. IB Gateway has TCP probe health check on port 4003. Redis has redis-cli PING health check. No external monitoring (Datadog, Grafana) is configured.

**Evidence**: `Dockerfile:55-56` -- HEALTHCHECK. `docker-compose.yml:80-85` -- IB Gateway healthcheck. `rust_core/src/engine.rs:334` -- last_heartbeat_ns.

**Status**: OPEN -- No external monitoring, alerting, or dashboarding exists. Engine failures are only detected by Docker restart. Need Prometheus/Grafana or at minimum email/SMS alerts for HALT events.

---

### Q-082 | DEPLOYMENT | What is the graceful shutdown protocol?

**Decision**: Docker stop_grace_period = 60 seconds. Engine should flatten all positions and wait for fill acknowledgments before exiting. SC-01a documents this as a shutdown requirement.

**Evidence**: `docker-compose.yml:37-38` -- stop_grace_period: 60s with SC-01a comment.

**Status**: DECIDED

---

### Q-083 | DEPLOYMENT | How is disk space managed on EC2?

**Decision**: EC2 has 19GB total. Docker builds consume ~5GB each. Manual `docker system prune -f` needed before builds when disk > 80%. Log rotation: json-file driver with max-size 10m/3 files for engine, 5m/2 files for IB Gateway, 5m/3 files for Redis.

**Evidence**: `docker-compose.yml:47-49` -- Logging config. Project memory: "19GB, Docker builds consume ~5GB each."

**Status**: OPEN -- No automated disk space monitoring or cleanup. A single forgotten prune before build could fill disk, crashing the engine and potentially corrupting the WAL.

---

### Q-084 | DEPLOYMENT | What is the IBKR secdef delay fix?

**Decision**: 15-second wait after connect() before subscribe_all() because the IBKR security definition farm (secdefeu) needs initialization time. Without this delay, subscriptions fail silently.

**Evidence**: Project memory: "15s wait after connect() before subscribe_all() (secdefeu farm needs time)".

**Status**: DECIDED

---

### Q-085 | DEPLOYMENT | What shared memory configuration is used?

**Decision**: Docker shm_size = 2GB for the engine container (SC-16). This supports shared memory IPC. The default Docker shm of 64MB would be insufficient for the PyO3 extension module and subprocess communication patterns.

**Evidence**: `docker-compose.yml:40` -- shm_size: '2gb'.

**Status**: DECIDED

---

## 8. ECONOMICS (Q-086 to Q-095)

### Q-086 | ECONOMICS | What is the true round-trip cost for LSE leveraged ETPs?

**Decision**: Estimated 0.3-0.5% per round trip. Composed of: IBKR commission (~0.05%), bid-ask spread (~0.2-0.4% for 3x ETPs), stamp duty (0% for ETPs), and slippage (~0.05-0.1%). The spread is the dominant cost component.

**Evidence**: `rust_core/src/exit_engine.rs:73` -- round_trip_fee_pct: 0.003 (0.3%). `config/config.toml:73` -- slippage_assumption_pct = 0.5. `config/strategies.toml:67` -- filter_spread_max_bps = 15 for VWAP DipBuy.

**Status**: OPEN -- The 0.3% exit engine assumption and 0.5% config assumption are inconsistent. Need empirical measurement from paper trades to determine true all-in costs per instrument.

---

### Q-087 | ECONOMICS | How does volatility drag affect leveraged ETP returns?

**Decision**: 3x ETP daily compounding drag factor = 9 (Kelly config), 5x = 25. Multi-day holds on 3x products cost 0.5-1% in decay per day. S19 RSI/IBS strategy applies 50% additional sizing penalty for 3x products and has max 10-day hold limit to control decay.

**Evidence**: `config/config.toml:29-30` -- volatility_drag_3x = 9, volatility_drag_5x = 25. `config/strategies.toml:198-199` -- sizing_mult = 0.6, sizing_3x_penalty = 0.5 for RSI/IBS.

**Status**: DECIDED

---

### Q-088 | ECONOMICS | What is the daily return target and is it realistic?

**Decision**: 0.3-0.5% daily net (145-348% annualized). The plan acknowledges 2% daily as a theoretical ceiling never achieved by any fund. With 3 trades/day max at 0.50% RT cost, the net daily budget after costs is roughly (3 trades x avg_win) - (3 x 0.5% cost).

**Evidence**: Project memory: "MVP Target: 0.3-0.5% daily net (145-348% annualised) -- realistic, world-class".

**Status**: DECIDED

---

### Q-089 | ECONOMICS | How does the system track cost basis?

**Decision**: SC-10 VWAP cost-basis tracker per ticker. Updated on each FillEvent with record_fill(price, qty, commission). Tracks total_cost, total_qty, total_commission, and computes VWAP. Cleared nightly + reqPositions resync.

**Evidence**: `rust_core/src/portfolio.rs:8-46` -- CostBasisEntry struct with record_fill() and net_cost_basis() methods.

**Status**: DECIDED

---

### Q-090 | ECONOMICS | How is the FX conversion handled for USD-denominated LSE ETPs?

**Decision**: Most LSE leveraged ETPs trade in USD on LSEETF exchange. FxRateTable handles multi-currency conversion (17 currencies supported). Per-ticker currency is cached in engine.ticker_currencies HashMap. Conversion to GBP happens for mark-to-market and PnL calculation.

**Evidence**: `rust_core/src/currency.rs:1-55` -- Currency enum with 17 variants. `rust_core/src/engine.rs:427-428` -- ticker_currencies: HashMap<TickerId, String>.

**Status**: DECIDED

---

### Q-091 | ECONOMICS | What is the minimum entry size?

**Decision**: 100 GBP in paper mode (reduced for Kelly ramp bootstrap). Live config.live.toml does not specify a minimum entry, but Kelly clamp_min = 0.15 (15% of equity = 1,500 GBP minimum at 10K). SC-05 minimum entry gate is suspended when validated_trades < 250.

**Evidence**: `rust_core/src/engine.rs:485` -- minimum_entry_gbp = 100.0 in paper. `config/config.live.toml:48` -- clamp_min = 0.15.

**Status**: DECIDED

---

### Q-092 | ECONOMICS | How does the system handle dividend withholding tax?

**Decision**: PortfolioState carries dividend_withholding_factor = 0.85 (UK ISA: 15% withholding). This adjusts dividend income calculations. Most leveraged ETPs do not pay dividends (they reinvest or are synthetic), making this largely theoretical for the current universe.

**Evidence**: `rust_core/src/portfolio.rs:70-71` -- dividend_withholding_factor: 0.85.

**Status**: DECIDED

---

### Q-093 | ECONOMICS | What is the Amihud illiquidity ratio used for?

**Decision**: Computed per ticker from bar history: mean(|return| / volume) over last 50 bars. Higher values indicate less liquid instruments. Passed to Python brain via TickContext.amihud for signal quality assessment and Kelly adjustment.

**Evidence**: `rust_core/src/engine.rs:246-269` -- amihud() method on BarHistory. `rust_core/src/python_bridge.rs:89` -- amihud field in TickContext.

**Status**: DECIDED

---

### Q-094 | ECONOMICS | Is overnight exposure capped?

**Decision**: Yes. overnight_exposure_cap_pct = 50.0 limits maximum equity held in overnight positions. This protects against gap risk on leveraged products where overnight moves can be 3-5x the underlying gap.

**Evidence**: `config/config.toml:69` -- overnight_exposure_cap_pct = 50.0.

**Status**: DECIDED

---

### Q-095 | ECONOMICS | What is the break-even win rate at current cost assumptions?

**Decision**: With 0.5% round-trip cost and targeting average wins of 1.0-1.5% (Rung 3-4 exits), the break-even win rate is approximately 33-50%. The confidence floor of 65 implies the system should only enter when estimated edge is well above break-even. At 3 trades/day, annual cost drag is 7,560 GBP on 10K equity, requiring ~76% return just to cover costs.

**Evidence**: `config/config.toml:76-78` -- Cost analysis comment. Chandelier rungs at `exit_engine.rs:64` define the win magnitude targets.

**Status**: OPEN -- The 76% annual cost drag at 3 trades/day is extremely high relative to the 10K equity base. Consider whether 1-2 trades/day would significantly improve net returns by reducing cost burden.

---

## 9. COMPLIANCE (Q-096 to Q-105)

### Q-096 | COMPLIANCE | How is the ISA annual contribution limit enforced?

**Decision**: 20,000 GBP annual limit tracked in PortfolioState.isa_year_invested, incremented on each position add. RiskArbiter CHECK 17 rejects entries when limit is reached. IsaGate.check() verifies per-trade against remaining allowance. Tax year starts April 6 (config isa_tax_year_start).

**Evidence**: `config/config.toml:23-24` -- isa_annual_limit_gbp = 20000, isa_tax_year_start = "04-06". `rust_core/src/isa_gate.rs:49-67` -- check() method. `rust_core/src/portfolio.rs:98-99` -- isa_year_invested tracking.

**Status**: DECIDED

---

### Q-097 | COMPLIANCE | Which exchanges are blocked for ISA eligibility?

**Decision**: Hard blocklist: TWSE, XTAI (Taiwan), XSHG, XSHE (China), XBOM, XNSE (India). These are not HMRC ISA-eligible. Blocked at the IsaGate level before any order is considered. European, US, HK, Japan, and LSE exchanges are allowed.

**Evidence**: `rust_core/src/isa_gate.rs:29-38` -- blocked_exchanges HashSet initialization.

**Status**: DECIDED

---

### Q-098 | COMPLIANCE | Is short selling prevented?

**Decision**: Yes, at multiple levels: (1) RiskArbiter CHECK 1 rejects Short direction and escalates to HALT, (2) OrderIntent only supports Long in ISA (Short exists for type completeness), (3) Inverse ETPs (QQQS.L, 3USS.L) provide regulatory-compliant synthetic short exposure.

**Evidence**: `rust_core/src/risk_arbiter.rs:122-126` -- CHECK 1. `rust_core/src/types/structs.rs:101` -- "Long only in ISA".

**Status**: DECIDED

---

### Q-099 | COMPLIANCE | How is the ISA tax year rollover handled?

**Decision**: IsaGate.new_tax_year() resets deposits_this_year_gbp to zero and updates tax_year_start. The nightly pipeline should detect April 6 boundary and trigger the reset. PortfolioState.isa_year_invested would need corresponding reset.

**Evidence**: `rust_core/src/isa_gate.rs:84-88` -- new_tax_year() method.

**Status**: OPEN -- No automated detection of April 6 tax year boundary exists in the cron schedule. Manual intervention would be required to reset the ISA allowance tracker each year.

---

### Q-100 | COMPLIANCE | Are leveraged ETPs permitted in a UK Stocks and Shares ISA?

**Decision**: Yes. LSE-listed ETPs (including leveraged and inverse products from providers like GraniteShares, WisdomTree, Leverage Shares) are ISA-eligible as they are listed on a recognised stock exchange. The IsaGate validates the exchange MIC, not the product type.

**Evidence**: `rust_core/src/isa_gate.rs:96-101` -- test_allowed_european_exchanges confirms XLON, XETR, XPAR, XAMS are allowed. `config/contracts.toml:24` -- LSEETF exchange for all core ETPs.

**Status**: DECIDED

---

### Q-101 | COMPLIANCE | Is there FCA compliance consideration?

**Decision**: As a personal trading system (not managing client money), FCA authorisation is not required. However, the system should not engage in market manipulation (wash trading, spoofing). The velocity check and daily trade limit naturally constrain trading frequency.

**Evidence**: `config/config.toml:78` -- max_daily_trades = 3 limits activity. No client money handling documented.

**Status**: DECIDED

---

### Q-102 | COMPLIANCE | How is stamp duty handled?

**Decision**: UK stamp duty (SDRT) of 0.5% applies to UK shares but NOT to ETPs. London-listed leveraged ETPs are exempt from stamp duty as they are structured as securities (not shares). No stamp duty calculation exists in the cost model because it is not applicable.

**Evidence**: Not explicitly coded. The cost model in exit_engine.rs and config.toml does not include stamp duty, which is correct for the ETP universe.

**Status**: DECIDED

---

### Q-103 | COMPLIANCE | Is the system compliant with IBKR API usage terms?

**Decision**: The system uses client_id=101, respects rate limits (50 msgs/sec, 10ms pacing on reqMktData, 60 historical data requests per 10 min), and operates within the 100 concurrent market data line limit. No automated workarounds for 2FA.

**Evidence**: `config/config.toml:113-119` -- IBKR rate limit configuration matching documented IBKR limits.

**Status**: DECIDED

---

### Q-104 | COMPLIANCE | How is the ISA contribution tracked across multiple positions?

**Decision**: PortfolioState.isa_year_invested is a cumulative counter incremented by (avg_entry * qty) on each position add. It does NOT decrease when positions are closed (ISA contributions are one-way). This correctly models HMRC rules where withdrawals reduce the available contribution space.

**Evidence**: `rust_core/src/portfolio.rs:96-99` -- add_position() increments isa_year_invested. remove_position() at lines 103-111 does NOT decrement it.

**Status**: OPEN -- Need to verify: does the ISA contribution tracker reset correctly if a trade is opened and then the fill is amended/cancelled? Partially filled orders could cause over-counting.

---

### Q-105 | COMPLIANCE | Is there audit trail compliance?

**Decision**: The WAL provides a complete, immutable, time-ordered audit trail of every order intent, risk decision, fill, position change, and system event. CRC32 checksums prevent tampering. Dead letter queue captures malformed data. Reconciliation audit log with 24-hour lock period adds additional oversight.

**Evidence**: `rust_core/src/wal_writer.rs:1-2` -- "Append-only ndjson event journal. Source of truth (H26)." `rust_core/src/reconciler.rs:44-100` -- ReconcileAuditLog with 24h lock.

**Status**: DECIDED

---

## 10. PAPER-TO-LIVE (Q-106 to Q-120)

### Q-106 | PAPER_TO_LIVE | What is the IS_LIVE guard mechanism?

**Decision**: IS_LIVE=false is hardcoded in main.rs:29 with an exit(1) guard. Transitioning to live requires: (1) code change in main.rs, (2) code review, (3) config.live.toml overlay verification, (4) 100-trade validation gate passage, (5) human sign-off.

**Evidence**: Project memory: "IS_LIVE=false hardcoded in main.rs:29 with exit(1) guard". `config/config.live.toml:15-18` -- Paper->Live transition checklist.

**Status**: DECIDED

---

### Q-107 | PAPER_TO_LIVE | What are the live configuration overrides?

**Decision**: config.live.toml overrides: max_simultaneous_positions = 3, portfolio_heat_limit_pct = 10.0, sector_heat_cap_pct = 33.0, cash_buffer_pct = 25.0, daily_trade_limit = 3, confidence_floor = 65, clamp_max = 0.20, clamp_min = 0.15.

**Evidence**: `config/config.live.toml:20-48` -- All live overrides with documentation.

**Status**: DECIDED

---

### Q-108 | PAPER_TO_LIVE | What is the 100-trade validation gate?

**Decision**: LiveReadinessGate requires: (1) >= 100 trades, (2) WR >= 40%, (3) Sharpe > 0, (4) max DD < 8%, (5) profit factor >= 1.0, (6) >= 63 days of paper trading, (7) all 16 Runtime Invariants verified, (8) human review completed. ALL criteria must pass.

**Evidence**: `rust_core/src/live_readiness.rs:50-75` -- LiveReadinessGate with exact thresholds.

**Status**: DECIDED

---

### Q-109 | PAPER_TO_LIVE | How does the startup assertion work?

**Decision**: RT1 safety item: when IS_LIVE=true, the engine loads config.toml then overlays config.live.toml. At startup, critical parameters are validated against expected live values. If config.live.toml is missing or contains PAPER VALIDATION values, the engine refuses to start.

**Evidence**: `config/config.live.toml:6-7` -- "If this file is missing or contains PAPER VALIDATION values, the engine MUST refuse to start."

**Status**: DECIDED

---

### Q-110 | PAPER_TO_LIVE | What risk parameters change between paper and live?

**Decision**: Major changes: max_positions 15 -> 3, portfolio_heat 50% -> 10%, sector_heat 80% -> 33%, cash_buffer 5% -> 25%. These dramatically reduce capital at risk. Trade limit, confidence floor, spread veto, and Kelly cap stay the same.

**Evidence**: `config/config.toml:18-22` vs `config/config.live.toml:20-28` -- Side-by-side comparison.

**Status**: DECIDED

---

### Q-111 | PAPER_TO_LIVE | Will paper trade results transfer to live?

**Decision**: Partially. Cost-critical parameters (spread veto at 0.3%, daily trade limit, confidence floor) are enforced identically. However, paper mode relaxes position count (15 vs 3), heat limits, and drawdown breakers. Paper results will overstate diversification benefits and understate concentration risk.

**Evidence**: `rust_core/src/engine.rs:475-490` -- Paper mode calibration with detailed comments on which parameters match live.

**Status**: OPEN -- The 15-position paper mode will produce very different correlation/drawdown characteristics than the 3-position live mode. Recommend a secondary validation period with paper positions capped at 3 before go-live.

---

### Q-112 | PAPER_TO_LIVE | How many paper trades exist so far?

**Decision**: 20 trades as of 2026-03-19 (dynamic_weights.toml bayesian.trade_count = 20). This is 20% of the 100-trade minimum required for the validation gate. At 3 trades/day max, reaching 100 trades requires approximately 34 more trading days.

**Evidence**: `config/dynamic_weights.toml:10` -- trade_count = 20.

**Status**: DECIDED

---

### Q-113 | PAPER_TO_LIVE | What is the Kelly ramp mechanism?

**Decision**: SC-13 Kelly scaling ramp prevents full-size positions before sufficient statistical evidence: ramp = clamp(validated_trades/250, 0.1, 1.0). At 20 trades, ramp = 0.08 (clamped to 0.1), meaning positions are 10% of full Kelly size. Full Kelly is only reached after 250 validated trades.

**Evidence**: `rust_core/src/risk_arbiter.rs:371` -- kelly_ramp calculation. `rust_core/src/engine.rs:483` -- kelly_ramp_trades = 250 in paper mode.

**Status**: DECIDED

---

### Q-114 | PAPER_TO_LIVE | What is the 63-day gauntlet?

**Decision**: Minimum 63 trading days (approximately 3 calendar months) of paper trading before live transition. This ensures the system operates through various market conditions (trending, mean-reverting, volatile, calm) and captures enough data for statistically meaningful evaluation.

**Evidence**: `rust_core/src/live_readiness.rs:73` -- min_days: 63.

**Status**: DECIDED

---

### Q-115 | PAPER_TO_LIVE | How will the IB Gateway transition from paper to live?

**Decision**: Currently TRADING_MODE=live in .env.production (for real market data). The engine's IS_LIVE=false + simulation_mode=true prevents real orders. For live trading: (1) keep TRADING_MODE=live, (2) set IS_LIVE=true in code, (3) set simulation_mode=false, (4) READ_ONLY_API remains false.

**Evidence**: `docker-compose.yml:5-6` -- "TRADING_MODE=live for real market data" + "IS_LIVE=false, simulation_mode=true". `docker-compose.yml:65` -- READ_ONLY_API=false.

**Status**: DECIDED

---

### Q-116 | PAPER_TO_LIVE | What is the maximum live capital allocation?

**Decision**: 10,000 GBP starting equity. ISA annual limit of 20,000 GBP caps total annual investment. With max 3 positions and 25% cash buffer, maximum simultaneous capital deployed is approximately 7,500 GBP. Kelly clamp_min of 15% sets minimum position at 1,500 GBP.

**Evidence**: `config/config.toml:183` -- starting_equity_gbp = 10000. `config/config.live.toml:28` -- cash_buffer_pct = 25.0.

**Status**: DECIDED

---

### Q-117 | PAPER_TO_LIVE | Is there a kill switch for emergency live shutdown?

**Decision**: Multiple layers: (1) RiskArbiter HALT state blocks all new entries, (2) FLATTEN forces all position closure, (3) Docker stop with 60s grace period, (4) IB Gateway can be stopped independently. No external kill switch (SMS/phone API) exists.

**Evidence**: `docker-compose.yml:37-38` -- stop_grace_period: 60s. `rust_core/src/risk_arbiter.rs:433-438` -- manual_clear_halt().

**Status**: OPEN -- No remote kill switch exists. If the operator loses SSH access to EC2, there is no way to halt trading. Consider implementing a dead man's switch or external monitoring service with kill capability.

---

### Q-118 | PAPER_TO_LIVE | What is the rollback plan if live trading fails?

**Decision**: Set IS_LIVE=false in code, redeploy. The engine will revert to simulation mode. All WAL data from the live period is preserved for post-mortem analysis. Positions opened during live may need manual closure via IBKR TWS if the engine cannot flatten them.

**Evidence**: `config/config.live.toml:15` -- "Set IS_LIVE=true in main.rs (requires code change + review)" implies reversal is also a code change.

**Status**: OPEN -- No formal rollback runbook exists. Need documented steps for: (1) emergency live->paper transition, (2) manual position closure procedure, (3) equity reconciliation after rollback.

---

### Q-119 | PAPER_TO_LIVE | How will the system handle the first live trading day?

**Decision**: The Crucible config section (crucible.paper_mode) controls the validation phase. The live readiness gate must pass ALL criteria. First live day should use the most conservative parameters from config.live.toml. No documented first-day-specific monitoring protocol exists.

**Evidence**: `rust_core/src/live_readiness.rs:78-100` -- evaluate() checks all criteria.

**Status**: DEFERRED -- First live day protocol will be defined after the 63-day gauntlet and 100-trade gate are passed.

---

### Q-120 | PAPER_TO_LIVE | What metrics define success or failure in the first month of live trading?

**Decision**: Success criteria from the Live Readiness Gate, applied continuously: WR >= 40%, Sharpe > 0, max DD < 8%, PF >= 1.0. Additional live-specific metrics: actual vs expected slippage, actual vs modeled spread costs, broker connectivity uptime, WAL integrity (zero CRC failures), reconciliation clean rate (100%).

**Evidence**: `rust_core/src/live_readiness.rs:66-74` -- Gate thresholds. These should be monitored continuously post go-live, not just at the gate.

**Status**: DEFERRED -- Post-launch KPI dashboard and monitoring alerts will be defined during the go-live preparation phase.

---

## SUMMARY STATISTICS

| Category | Questions | DECIDED | OPEN | DEFERRED |
|----------|-----------|---------|------|----------|
| ARCHITECTURE | Q-001 to Q-015 | 15 | 0 | 0 |
| RISK_MANAGEMENT | Q-016 to Q-030 | 15 | 0 | 0 |
| SIGNAL_GENERATION | Q-031 to Q-045 | 13 | 2 | 0 |
| ORDER_LIFECYCLE | Q-046 to Q-055 | 9 | 1 | 0 |
| WAL_PERSISTENCE | Q-056 to Q-065 | 9 | 1 | 0 |
| OUROBOROS | Q-066 to Q-075 | 9 | 1 | 0 |
| DEPLOYMENT | Q-076 to Q-085 | 8 | 2 | 0 |
| ECONOMICS | Q-086 to Q-095 | 8 | 2 | 0 |
| COMPLIANCE | Q-096 to Q-105 | 8 | 2 | 0 |
| PAPER_TO_LIVE | Q-106 to Q-120 | 10 | 3 | 2 |
| **TOTAL** | **120** | **104** | **14** | **2** |

---

## OPEN ITEMS REQUIRING ACTION

| # | Question | Risk Level | Action Required |
|---|----------|------------|-----------------|
| Q-031 | Gap Fade enabled=true in strategies.toml but documented as disabled | LOW | Reconcile config with audit decision |
| Q-045 | No Python subprocess auto-restart | MEDIUM | Implement bridge.py crash detection + restart |
| Q-051 | Inconsistent cost model (0.3% vs 0.5%) | MEDIUM | Unify RT cost across exit_engine and config |
| Q-064 | No WAL schema migration tooling | LOW | Build migration tool before schema_version=2 |
| Q-068 | Normal regime scale 1.60 on 20 trades | HIGH | Revert to 1.0 until trade_count >= 50 |
| Q-073 | Ouroboros can lower confidence_floor below 65 | HIGH | Guard: dynamic floor >= static floor |
| Q-081 | No external monitoring or alerting | MEDIUM | Add Prometheus/alerting for HALT events |
| Q-083 | No automated disk space management | MEDIUM | Add cron-based docker prune + disk alerts |
| Q-086 | Inconsistent cost assumptions | MEDIUM | Measure empirical RT costs from paper data |
| Q-095 | 76% annual cost drag at 3 trades/day | HIGH | Evaluate 1-2 trades/day regime |
| Q-099 | No automated ISA tax year rollover | LOW | Add April 6 detection in cron schedule |
| Q-104 | ISA contribution over-counting risk | LOW | Add fill amendment handling |
| Q-111 | 15-pos paper vs 3-pos live mismatch | HIGH | Run secondary validation at 3-pos cap |
| Q-117 | No remote kill switch | HIGH | Implement dead man's switch / external monitor |

## DEFERRED ITEMS

| # | Question | Precondition |
|---|----------|--------------|
| Q-119 | First live day protocol | 63-day gauntlet + 100-trade gate passed |
| Q-120 | Post-launch KPI definitions | Go-live preparation phase reached |

---

*Document generated: 2026-03-20T00:00:00Z*
*Next review: After 100-trade validation gate is reached*
