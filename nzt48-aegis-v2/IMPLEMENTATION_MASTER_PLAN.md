# AEGIS V2 — IMPLEMENTATION MASTER PLAN v6.0
# 13-Phase State-Machine Execution: Full Audit → Build → Validate
**Generated:** 2026-03-20 | **Version:** 6.0 (Institutional ULTRATHINK Run)
**Board:** CTO, CRO, CIO, Head of Quant, Head of Execution, Head of SRE, Head of AI Design, Red-Team, Model Risk
**Evidence standard:** PROVEN/LIKELY/SPECULATIVE/NEEDS TEST with file:line references
**N0 Survival Stack:** DEPLOYED 2026-03-20 (commit 8c50a66)
**Prior Adversarial:** 371 points (103 + 268) — processed in v5.1
**Codebase:** 30,137 Rust LOC (79 files) + 20,175 Python LOC (51 files) = 50,312 total

---

## PHASE 0 COMPLETE — INGESTION SUMMARY

### Files Ingested (Complete)

**Rust Core (79 files, 30,137 LOC):**
- engine.rs (2,944) — main event loop, 8-step startup, tick dispatch
- ibkr_broker.rs (1,358) — IBKR TWS/Gateway connection, rate limiter
- strategy_config.rs (1,110) — strategy definitions, universe tiers
- main.rs (930) — binary entrypoint, WAL rotation, signal handlers
- crucible.rs (777) — signal validation, deterministic replay
- exit_engine.rs (748) — Chandelier 5-rung, collision resolution, InfiniteChandelier
- scanner.rs (560) — HotScanner, RotationScanner
- market_scheduler.rs (538) — session scheduling, trading modes
- entry_engine.rs (531) — Kelly sizing, entry types
- wal_actor.rs (497) — async WAL writer thread, crossbeam channel
- risk_arbiter.rs (493) — 31-check fail-closed gate, 4-state regime
- python_bridge.rs (487) — subprocess IPC, BrainSignal handling
- portfolio.rs (473) — position tracking, equity management
- subscription_manager.rs (472) — 100-line rotation
- clock.rs (396) — IBKR clock sync, BST transitions
- wal_replay.rs (389) — crash recovery, event replay
- reconciler.rs (387) — 5-min audit loop, orphan detection
- position_sizer.rs (346) — Kelly criterion, confidence scaling
- wal_writer.rs (280) — ndjson append, CRC32, fsync
- types/wal.rs (251) — 19 WAL event types
- types/enums.rs (529) — Direction, RiskRegime, VetoReason, etc.
- Plus 58 more modules (regime, vol, garch, sessions, hardening, etc.)

**Python Brain (51 files, 20,175 LOC):**
- ticker_selector.py (1,419) — 4-tier universe scoring
- full_universe_builder.py (1,311) — ISA universe discovery
- bridge.py (1,040) — signal generation, 10+ gates, VanguardSniper + Orchestrator
- nightly_v6.py (1,010) — Ouroboros nightly learning loop
- sheets_sync.py (927) — Google Sheets drain
- autonomous_orchestrator.py (820) — S17-S20 strategies
- config_writer.py (793) — dynamic_weights.toml generation
- indicator_intelligence.py (1,016) — rule discovery
- persistent_memory.py — cumulative system knowledge
- Plus 41 more modules

**Configuration:**
- config/config.toml (184 lines, all parameters)
- config/contracts.toml (303 contracts, 49 LSE ETPs)
- config/strategies.toml (strategy parameters)
- config/dynamic_weights.toml (Ouroboros output)
- crontab (22 scheduled jobs)
- entrypoint.sh (boot sequence)
- docker-compose.yml (3 containers)
- Dockerfile (build pipeline)

**Documentation:**
- AEGIS_V2_MASTER_PLAN.md (3,097 lines, v5.1)
- docs/DEPLOYMENT.md (444 lines)
- docs/OPERATIONS.md (418 lines)
- docs/TESTING.md (365 lines)

### Assumptions to Test (Not Trust)

1. 79% win rate on 20 trades → 95% CI is [55%, 94%]. NEEDS 100+ trades.
2. MTF gate eliminates ~40% of signals → unknown if these are losers or winners. NEEDS gate_vetoes analysis.
3. VanguardSniper base confidence 70 → no backtest validation. NEEDS backtesting.
4. Chandelier rung thresholds [0, 0.8%, 1.5%, 2.5%, 4%] → optimized theoretically, not empirically. NEEDS trade data.
5. 12-factor Kelly → paper bootstrap floor bypasses when trades < 50. NEEDS evaluation post-50 trades.
6. PAPER VALIDATION overrides (15 positions, 50% heat) → must revert for live. NEEDS config.live.toml.

---

## PHASE 1 COMPLETE — EXECUTIVE TRUTH

### What This Machine Currently Is

AEGIS V2 is a **real, partially operational autonomous trading engine** with institutional-grade architecture (A grade) but **unvalidated economics** (D+ grade on cost modeling, improving post-N0). It is a Rust+Python event-driven system that connects to Interactive Brokers via IB Gateway, receives 5-second market data ticks, generates signals through a Python subprocess bridge, validates through a 31-check risk arbiter, executes via IBKR API, manages positions with a 5-rung Chandelier trailing stop, and persists all state via Write-Ahead Log event sourcing with CRC32 integrity and fsync guarantees.

### Current Stage

**Late prototype / early paper validation.** The architecture is complete and deployed. N0 Survival Stack (trade cap, cost fields, confidence floor) was deployed 2026-03-20. Only 20 trades in history. The system needs 100+ cost-tracked trades to validate profitability.

### Is It Close to Being a Compounding Machine?

**No, not yet.** The distance is approximately 8-12 weeks of paper trading + 5-10 days of build work. The architecture is excellent but the economics are unproven.

### Top 5 Reasons It Is Not There Yet

1. **Only 20 trades.** Statistical insignificance. Cannot distinguish skill from luck.
2. **Ouroboros learns gross, not net.** Cannot distinguish spread victims from genuine losers. N0 added cost fields but learning not yet cost-aware.
3. **No missed-winner analysis.** 40%+ of signals suppressed by gates with no counterfactual tracking of whether suppressed signals would have won.
4. **No benchmark context at trade time.** SPY/QQQ direction, VIX level, sector momentum not captured in WAL.
5. **Bar history lost on restart.** 200-tick warmup (16 min) means no signals for 16 minutes after every container restart.

### Top 5 Reasons It Still Has Promise

1. **Crash-safe WAL with idempotent replay.** Production-grade persistence that many institutional systems lack.
2. **31-check fail-closed risk arbiter.** Comprehensive, deterministic, tested (30 unit tests, 95% coverage).
3. **5-rung Chandelier with rung persistence.** Trailing stops survive restarts. Adaptive multipliers ready.
4. **Ouroboros learning loop is WIRED.** nightly_v6 → config_writer → dynamic_weights.toml → SIGHUP hot-reload. The pathway exists, it just needs better data.
5. **ISA tax advantage.** 0% CGT on all gains. The system's structural edge is the tax wrapper, not alpha generation.

---

## PHASE 2 COMPLETE — SYSTEM ARCHITECTURE

### Real Control Flow

```
BOOT: entrypoint.sh
  → supercronic (cron daemon, background)
  → persistent_memory print (knowledge summary)
  → config_writer (pre-boot refresh of dynamic_weights.toml)
  → wal_watcher (Telegram notifications, background)
  → exec aegis (PID 1, main engine)

ENGINE STARTUP (8 steps):
  1. Load config (config.toml, contracts.toml, strategies.toml, dynamic_weights.toml)
  2. Initialize WAL writer + WAL actor thread
  3. Connect to IBKR (IB Gateway port 4003)
  4. Sync clock (broker time offset)
  5. Replay WAL events (reconstruct positions, regime, rungs)
  6. Reconcile with broker (detect mismatches → HALT if found)
  7. Subscribe to market data (tier1 permanent + tier2 rotating)
  8. Spawn Python subprocess (bridge.py)
  9. Write SystemReady WAL event

MAIN LOOP (100ms tick):
  Tick arrives from IBKR → MarketTick
    → Update bar history (per-ticker deque)
    → Update MAE/MFE for open positions
    → Send to Python bridge (JSON over stdin)
    → Python returns signal or no_signal
    → If signal:
      → RiskArbiter.evaluate() [31 checks, <1ms]
      → If approved: Kelly sizing → Order submission → WAL RoutedOrder
      → If rejected: Log veto reason to gate_vetoes.ndjson
    → For each open position:
      → ExitEngine.update_tracking() [update highest_high, rung, stop]
      → ExitEngine.evaluate() [check all exit conditions]
      → If exit triggered: Submit exit order → WAL ExitSignal
    → Process broker events (fills, acks, errors)
    → Every 5 min: Reconciliation check
    → Every 1 hour: StateSnapshot WAL event

SHUTDOWN:
  SIGINT/SIGTERM → graceful shutdown
    → Flatten all positions (3-phase EOD)
    → Wait for pending fills (up to 60s)
    → Write SystemShutdown WAL event
    → Close WAL writer
```

### What Is Truly Wired vs. What Only Appears Wired

| Component | Truly Wired? | Evidence |
|-----------|-------------|---------|
| WAL event sourcing | ✅ YES | 19 event types, CRC32, fsync, idempotent replay |
| Risk Arbiter 31 checks | ✅ YES | 30 unit tests, deterministic evaluation |
| Chandelier 5-rung exit | ✅ YES | Rung persistence via WAL, tested (32 tests) |
| Python bridge signal generation | ✅ YES | JSON over stdin/stdout, 10+ gates |
| Ouroboros nightly learning | ✅ YES | nightly_v6 → config_writer → dynamic_weights.toml → SIGHUP |
| Google Sheets sync | ✅ YES | sheets_sync.py runs every 5 min via cron |
| Ticker blacklist | ⚠️ PARTIAL | Written to config, NOT checked in bridge.py |
| UK holidays | ❌ NO | uk_holidays.toml exists, not consumed by engine |
| Economic calendar filters | ❌ NO | strategies.toml flags defined, no data source |
| Bridge SIGHUP reload | ❌ NO | Engine reloads, bridge does NOT |
| Bar history persistence | ❌ NO | Lost on restart, 16-min warmup required |
| Macro/event veto | ❌ NO | Cross-asset macro exists but not wired to veto |
| Claude integration | ❌ NO | Not integrated anywhere yet |

---

## PHASE 3 COMPLETE — END-TO-END TRADE LIFECYCLE

### Complete Trace: QQQ3.L Long Entry → Exit

**1. Clock/Session (clock.rs:190)**
Engine checks: 10:30 London → ModeB (main LSE trading). Entry allowed.

**2. Exchange Availability (market_scheduler.rs)**
LSE open 08:00-16:30 London. QQQ3.L trades on LSEETF. Active.

**3. Ranking/Watchlist (ticker_selector.py)**
QQQ3.L is ticker_id=0 (Core 12, Tier 1 permanent). Always subscribed.

**4. Signal Generation (bridge.py:517-943)**
Tick arrives → bar_history[0].append(tick)
→ Aggregate 60 × 5s bars into 5-min bars
→ Compute: ADX(14)=32, Hurst=0.58 (trending), RVOL=1.4, vol_slope=+2.1
→ Gate checks:
  - Warmup: 200+ ticks ✅
  - Indicator gates: hurst > 0.50 ✅
  - VWAP extension: price 0.3% above VWAP ✅ (< 1.5%)
  - Hurst regime: 0.58 > 0.40 ✅
  - Volume slope: +2.1 > 0 ✅
  - MTF alignment: 5s↑ 1m↑ 5m↑ ✅
→ VanguardSniper evaluates: confidence=76
→ LSE leveraged boost: +20 → confidence=96 (capped 100 by risk arbiter)
→ Kelly 12-factor: kelly_fraction=0.045, shares=18
→ Post-signal: spread 0.15% < 2.0% ✅, extension 0.8% < 3.0% ✅, cooldown clear ✅
→ Returns signal: {type:"signal", confidence:96, kelly:0.045, shares:18, strategy:"VanguardSniper"}

**5. Risk Arbiter (risk_arbiter.rs:96-400)**
31 checks in deterministic order:
- CHECK 1: ISA Short → Long ✅
- CHECK 6: Max positions (15) → 2 open ✅
- CHECK 7: Stale data → last tick 2s ago ✅
- CHECK 10: Confidence floor (65) → 96 ✅
- CHECK 11: Time cutoff (15:45) → 10:30 ✅
- CHECK 13: Spread veto (0.3%) → 0.15% ✅
- CHECK 18: Daily drawdown (4%) → 0.2% ✅
- CHECK 28: Daily trades (3) → 1 today ✅
- All 31 pass → APPROVED, adjusted_size=18 shares

**6. Order Submission (entry_engine.rs → ibkr_broker.rs)**
Limit price = mid + 0.1% buffer = 25.13
Submit LMT BUY 18 QQQ3 @ 25.13 LSEETF
WAL: RoutedOrder {order_id, ticker_id:0, side:"Buy", confidence:96, kelly:0.045, qty:18, symbol:"QQQ3.L", currency:"USD", entry_rvol:1.4, entry_hurst:0.58, entry_adx:32}

**7. Fill (engine.rs → portfolio.rs)**
Broker ACK → WAL: BrokerAck {order_id, status:"Submitted", ibkr_order_id:12345}
Fill arrives: 18 @ 25.12, commission=£1.20
WAL: FillEvent {order_id, filled_qty:18, price:25.12, commission:1.20, spread_at_fill_pct:0.15, side:"BUY"}
Portfolio: Create PositionState {ticker_id:0, qty:18, avg_entry:25.12, highest_high:25.12, stop_price:24.50 (entry - 1.5×ATR), trailing_rung:0, mae:0, mfe:0}

**8. Position Lifecycle (exit_engine.rs:319-347)**
Every tick:
  - Update highest_high (if new high)
  - Compute new rung (can only increase)
  - Compute new stop (ratchet UP only, H68)
  - Update MAE/MFE

Tick @ 25.35 (+0.9%): Rung advances 0→2 (breakeven+fees lock)
WAL: RungAdvanced {ticker_id:0, old_rung:0, new_rung:2, stop_price:25.16, highest_high:25.35}

Tick @ 25.50 (+1.5%): Rung advances 2→3 (compounding unit)
WAL: RungAdvanced {ticker_id:0, old_rung:2, new_rung:3, stop_price:25.20, highest_high:25.50}

**9. Exit (exit_engine.rs:198-317)**
Tick @ 25.18 (price dropped through Chandelier stop at 25.20):
ExitEngine.evaluate() → ChandelierTrailing fires
Submit SELL 18 QQQ3 @ LMT 25.18
WAL: ExitSignal {ticker_id:0, reason:"ChandelierTrailing"}

Fill: 18 @ 25.17, commission=£1.20
WAL: PositionClosed {ticker_id:0, final_pnl:£0.50, gross_pnl:£0.90, total_commission:£2.40, spread_at_entry_pct:0.15, spread_at_exit_pct:0.18, entry_price:25.12, exit_price:25.17, highest_rung:3, mae:-0.24, mfe:+0.38, entry_rvol:1.4, entry_hurst:0.58, entry_adx:32, strategy:"VanguardSniper"}

**10. Reporting (sheets_sync.py)**
Every 5 min: Redis queue → Google Sheets (Trades tab, Open_Positions tab, Daily P&L tab)

**11. Nightly Learning (nightly_v6.py, 04:50 UTC)**
Read today's WAL → Extract all PositionClosed events
Trade analysis: QQQ3.L +£0.50, rung 3 reached, strategy VanguardSniper
Update: ticker_stats[QQQ3.L] wins+=1, total_pnl+=0.50
Regime accuracy check: predicted trending, was trending ✅
Parameter optimization: Kelly drift within 15% guardrail ✅
Write: ouroboros_recommendations.json

**12. Config Writer (config_writer.py, 04:51 UTC)**
Read recommendations → Update dynamic_weights.toml
- bayesian.win_rate → updated Bayesian WR
- exit.chandelier_atr_mult → adjusted if rung data suggests
- kelly_fractions.t1 → updated from ticker performance
Atomic write → SIGHUP → Engine reloads at next boot

---

## PHASE 4 COMPLETE — HONEST SYSTEM QUALITY REVIEW

| Dimension | Grade | Evidence |
|-----------|-------|---------|
| Architecture | **A** | 80 modules, newtypes, 42 VetoReason variants, generic BrokerAdapter |
| Strategy Quality | **C+** | VanguardSniper untested in backtest. Orchestrator strategies wired but unproven. |
| Code Quality | **A-** | Zero unwrap()/panic() in production. 174 Rust + 136 Python tests. 17 dead_code suppressions. |
| Deployment | **A-** | Docker Compose, health checks, graceful shutdown. PAPER overrides are time bombs. |
| Data Quality | **D+** | Only 20 trades. No backfill. No benchmark context. Cost fields added in N0 but empty. |
| Robustness | **A** | WAL crash recovery, 31-check risk gate, idempotent replay, orphan detection |
| Observability | **C** | Gate vetoes logged. No dashboard. No anomaly detection. Sheets sync exists but minimal. |
| Reporting | **C+** | Session PDFs, daily sim report exist. No winner/loser forensics. No structural analysis. |
| Adaptability | **B** | Ouroboros learns and adapts Kelly/chandelier/regime. But gross-only, not net. |
| Execution Realism | **B-** | Paper mode with relaxed limits. LSE ETP currency fix done. Spread veto at 0.3%. |
| Compounding Fitness | **D** | Cannot compound what you cannot measure. Cost-blind until N0. Unproven economics. |

### Critical Quality Findings

1. **ibkr_broker.rs has ZERO test coverage** (1,358 lines). Only path to real money.
2. **Integration tests are toy mocks** — test_integration.rs uses separate MockBroker/MockDataFeed.
3. **310 unwrap()/expect() calls** across 35 files, but ALL in test code. Zero in production.
4. **17 #[allow(dead_code)]** annotations — speculative code in config_loader, ouroboros_loader.
5. **.env has 10+ plaintext credentials** — properly gitignored, never committed, but needs secrets manager.
6. **BST transition dates hardcoded through 2032** — latent failure in clock.rs.
7. **PAPER VALIDATION overrides** (15 positions, 50% heat) — manual revert required for live.

---

## PHASE 5 — FORENSIC TELEMETRY AUDIT + IMPLEMENTATION

### Current State of Logging

**What IS logged (in WAL):**
- RoutedOrder: order_id, ticker_id, side, confidence, strategy, kelly_fraction, qty, symbol, currency, entry_rvol, entry_hurst, entry_adx
- FillEvent: order_id, ticker_id, filled_qty, remaining_qty, price, exec_id, commission, spread_at_fill_pct, side
- PositionClosed: ticker_id, final_pnl, gross_pnl, total_commission, spread_at_entry/exit_pct, entry/exit_price, highest_rung, mae, mfe, entry_rvol/hurst/adx, daily_trade_number, symbol, qty, regime_at_entry, confidence, strategy, exchange
- RungAdvanced: ticker_id, old_rung, new_rung, stop_price, highest_high
- RiskStateChange: from, to, trigger
- StateSnapshot: portfolio_json, equity, high_water, hash, open_positions

**What is logged OUTSIDE WAL:**
- gate_vetoes.ndjson: ticker_id, symbol, gate, price, detail, indicators (hurst, adx, rvol, vol_slope, spread, vwap_dist, etc.)

### What Is MISSING (Critical Gaps)

**At Signal Time (not captured):**
1. `benchmark_spy_return_pct` — SPY return since session open
2. `benchmark_qqq_return_pct` — QQQ return since session open
3. `vix_level` — VIX at signal time
4. `sector_momentum_pct` — sector ETF return (e.g., XLK for tech)
5. `session_phase` — which session window (morning/midday/afternoon/close)
6. `time_since_open_mins` — minutes since exchange open
7. `bars_since_last_signal` — gap between signals (signal frequency)
8. `atr_pct` — ATR as percentage of price (volatility-normalized)
9. `volume_profile_percentile` — where current volume sits in 20-day distribution
10. `mtf_alignment_score` — -3 to +3 (all bearish to all bullish)

**At Veto Time (not captured):**
11. `veto_reason` — which of the 31 checks triggered rejection
12. `veto_context` — full EvalContext snapshot at rejection
13. `would_have_been_signal` — the suppressed signal's confidence/strategy
14. `price_at_veto` — current price when rejected
15. `price_1h_after_veto` — price 1 hour later (counterfactual — requires deferred write)

**At Position Close (partially captured, needs enrichment):**
16. `hold_time_mins` — total position duration in minutes
17. `hold_time_bars` — total 5-min bars held
18. `entry_session_phase` — session phase at entry
19. `exit_session_phase` — session phase at exit
20. `price_path_type` — classified: "direct_hit", "pullback_then_run", "grind_up", "spike_then_fade"
21. `vix_at_entry` — VIX level when position opened
22. `vix_at_exit` — VIX level when position closed
23. `benchmark_return_during_hold` — SPY return over holding period
24. `correlation_to_spy_during_hold` — realized correlation during trade

### What Is Noise (Should NOT Be Added)

- Tick-level P&L snapshots (too granular, WAL would explode)
- Every bid/ask change (use 5-sec aggregated bars instead)
- Duplicate gate veto logs (already rate-limited in bridge.py)
- Historical indicator values at every tick (use entry snapshot only)

### BUILD NOW: New WAL Event Types

Two new WAL payload variants needed:

```rust
// NEW: Signal rejected by RiskArbiter
SignalRejected {
    ticker_id: u32,
    confidence: f64,
    strategy: String,
    veto_reason: String,       // Which check failed
    veto_check_number: u8,     // Check 1-31
    price: f64,
    spread_pct: f64,
    regime: String,
    session_phase: String,
    benchmark_context: String,  // JSON: {spy_ret, qqq_ret, vix}
},

// NEW: Missed winner tracking (deferred write, 1h after veto)
MissedWinnerCandidate {
    ticker_id: u32,
    original_confidence: f64,
    original_strategy: String,
    veto_reason: String,
    price_at_veto: f64,
    price_1h_later: f64,
    would_have_pnl_pct: f64,   // (price_1h - price_veto) / price_veto
    timestamp_ns: u64,
},
```

### BUILD NOW: Enriched PositionClosed Fields

Add to existing PositionClosed WAL payload:

```rust
// Additions to PositionClosed (all #[serde(default)])
hold_time_mins: u32,
entry_session_phase: String,    // "morning", "midday", "afternoon", "close"
exit_session_phase: String,
vix_at_entry: f64,
vix_at_exit: f64,
benchmark_spy_return_pct: f64,  // SPY return during hold
atr_pct_at_entry: f64,          // ATR/price at entry
volume_percentile: f64,         // 0-100, where volume sat at entry
mtf_alignment_score: i8,        // -3 to +3
```

---

## PHASE 6 — INDICATOR INTELLIGENCE + WINNER/LOSER FRAMEWORK

### Current Indicators (Used in Bridge)

| Indicator | Source | Used For | Actually Drives Decisions? |
|-----------|--------|----------|---------------------------|
| ADX(14) | bridge.py _compute_adx() | VanguardSniper trend strength | ✅ Yes (via vanguard_evaluate) |
| Hurst exponent | brain.indicators.hurst | Regime classification (trending/random/mean-reverting) | ✅ Yes (gate at 0.40) |
| RVOL | brain.indicators.volume_analytics | Relative volume vs 20-day mean | ⚠️ Indirect (volume slope gate uses raw slope) |
| Volume slope | bridge.py linear regression | Rising/falling volume | ✅ Yes (gate: must be > 0 for momentum) |
| VWAP distance | bridge.py VWAPCalculator | Extension detection | ✅ Yes (gate: reject if > 1.5% above) |
| MTF EMA alignment | bridge.py inline | Multi-timeframe confirmation | ✅ Yes (gate: all 3 must agree) |
| Spread % | bridge.py inline | Execution cost filter | ✅ Yes (gate: reject if > 2% for leveraged) |
| Vol divergence | brain.indicators.volume_analytics | Price-volume divergence | ❌ Computed but NOT used in any gate |
| RSI(2) | brain.rsi_ibs | Oscillator for S19 (Orchestrator) | ⚠️ Only in Orchestrator, not VanguardSniper |
| IBS | brain.rsi_ibs | Internal bar strength | ⚠️ Only in Orchestrator |
| Gap % | brain.gap_detector | Overnight gap detection | ⚠️ Only in Orchestrator (S18) |

### Indicators That SHOULD Be Added

1. **ATR as % of price** — normalizes volatility across different-priced instruments
2. **Bollinger Band position** — where price sits in 2σ bands (0-100)
3. **Volume-weighted momentum** — price change × volume (filters low-volume moves)
4. **Spread-to-range ratio** — spread / (high - low). High = noise environment, low = clean trends
5. **Time-since-last-fill** — detects stale/illiquid tickers

### Winner/Loser Taxonomy

**BUILD NOW: Trade Classification System**

```python
# python_brain/ouroboros/trade_taxonomy.py

class TradeOutcomeClass:
    # Winners
    CLEAN_TREND_WINNER = "clean_trend"        # Rung 3+, < 20% MAE/MFE ratio
    GRIND_WINNER = "grind"                     # Rung 2-3, > 50% MAE/MFE ratio
    SPIKE_WINNER = "spike"                     # Rung 4+, < 5 bars held
    LUCKY_WINNER = "lucky"                     # Won but regime was wrong

    # Losers
    STOP_HUNT_LOSER = "stop_hunt"             # MAE > 2×ATR then reversal within 30 bars
    SPREAD_VICTIM = "spread_victim"            # Loss < 2×spread (killed by friction)
    THESIS_FAILURE = "thesis_failure"          # Regime changed, trend reversed
    LATE_ENTRY = "late_entry"                  # Entry > 70% of session elapsed
    NOISE_EXIT = "noise_exit"                  # Stopped out < Rung 2, < 10 bars held
    OVEREXTENSION = "overextension"            # Entry > 1% above VWAP

    # Anomalies
    GAP_AGAINST = "gap_against"               # Overnight gap against position
    FLASH_CRASH = "flash_crash"               # > 3% drop in < 1 min
    CORRELATION_BREAK = "corr_break"          # Ticker decorrelated from benchmark mid-trade

    @classmethod
    def classify(cls, trade):
        """Classify a closed trade into taxonomy."""
        pnl = trade.get("final_pnl", 0)
        mae = abs(trade.get("mae", 0))
        mfe = abs(trade.get("mfe", 0))
        spread_entry = trade.get("spread_at_entry_pct", 0)
        rung = trade.get("highest_rung", 0)
        hold_mins = trade.get("hold_time_mins", 0)
        atr_pct = trade.get("atr_pct_at_entry", 0.01)

        if pnl > 0:
            if rung >= 4 and hold_mins < 25:
                return cls.SPIKE_WINNER
            elif rung >= 3 and (mae / max(mfe, 0.001)) < 0.20:
                return cls.CLEAN_TREND_WINNER
            elif rung >= 2:
                return cls.GRIND_WINNER
            else:
                return cls.LUCKY_WINNER
        else:
            loss_pct = abs(pnl) / max(trade.get("entry_price", 1) * trade.get("qty", 1), 1) * 100
            if loss_pct < 2 * spread_entry:
                return cls.SPREAD_VICTIM
            elif mae > 2 * atr_pct and hold_mins < 50:
                return cls.STOP_HUNT_LOSER
            elif rung < 2 and hold_mins < 50:
                return cls.NOISE_EXIT
            else:
                return cls.THESIS_FAILURE
```

### Structural Tradability Score

**BUILD NOW: Pre-Entry Quality Score**

```python
def structural_tradability_score(indicators):
    """Score 0-100: how structurally tradable is this setup?

    High score = clean trend, good liquidity, regime aligned, friction manageable.
    Low score = noisy, illiquid, regime confused, friction dominates.
    """
    score = 50  # Neutral baseline

    # Spread-to-range: low = clean, high = noisy
    str_ratio = indicators.get("spread_to_range", 0.5)
    score += 15 * (1 - min(str_ratio, 1.0))  # Max +15 for clean

    # Regime clarity: trending or mean-reverting clear > random
    hurst = indicators.get("hurst", 0.5)
    regime_clarity = abs(hurst - 0.5) * 2  # 0 at random, 1 at extremes
    score += 10 * regime_clarity

    # Volume confirmation: rising volume = real move
    vol_slope = indicators.get("vol_slope", 0)
    score += 10 * min(max(vol_slope / 5.0, -1), 1)  # ±10

    # MTF alignment: all agree = +10, disagree = -10
    mtf = indicators.get("mtf_alignment_score", 0)
    score += (mtf / 3.0) * 10

    # ADX strength: strong trend = tradable
    adx = indicators.get("adx", 20)
    if adx > 25:
        score += min((adx - 25) / 25.0, 1.0) * 10  # Max +10

    # ATR as % of price: moderate vol = good, extreme = bad
    atr_pct = indicators.get("atr_pct", 0.01)
    if 0.005 < atr_pct < 0.03:
        score += 5  # Sweet spot
    elif atr_pct > 0.05:
        score -= 10  # Too volatile

    return max(0, min(100, score))
```

---

## PHASE 7 — DASHBOARD / REPORTING DESIGN

### Google Sheets Tab Architecture (21 Tabs)

| # | Tab Name | Update Freq | Purpose | Ouroboros Reads? | Claude Reads? |
|---|----------|-------------|---------|-----------------|---------------|
| 1 | Daily_P&L | Every 5 min | Running P&L, equity curve | ✅ | ✅ |
| 2 | Open_Positions | Every 5 min | Current positions, unrealized P&L, rung, stop | ✅ | ✅ |
| 3 | Closed_Trades | On close | Full trade detail with all WAL fields | ✅ | ✅ |
| 4 | Win_Indicators | Nightly | Indicator values for all winning trades | ✅ | ✅ |
| 5 | Loss_Indicators | Nightly | Indicator values for all losing trades | ✅ | ✅ |
| 6 | Win_Loss_Delta | Nightly | Paired comparison: what differs between W and L | ✅ | ✅ |
| 7 | Rejected_Signals | On reject | Every rejected signal with full context | ✅ | ✅ |
| 8 | Missed_Winners | Nightly+1h lag | Rejected signals that would have won | ✅ | ✅ |
| 9 | MAE_MFE | On close | MAE/MFE per trade, per setup, per session | ✅ | ✅ |
| 10 | Session_Quality | Nightly | WR/PF/expectancy by session phase | ✅ | ✅ |
| 11 | Exchange_Quality | Nightly | WR/PF/expectancy by exchange | ✅ | ✅ |
| 12 | Strategy_Quality | Nightly | WR/PF/expectancy by strategy | ✅ | ✅ |
| 13 | Spread_Execution | Nightly | Spread at entry/exit, slippage, commission | ✅ | ✅ |
| 14 | Macro_Context | Nightly | VIX, SPY, event calendar at each trade | ✅ | ✅ |
| 15 | Anomaly_Review | Nightly | Flash crashes, gap-against, correlation breaks | ✅ | ✅ |
| 16 | Parameter_History | Nightly | Rolling Ouroboros parameter changes | ✅ | ✅ |
| 17 | Recommendations | Nightly | Ouroboros recs + applied/ignored status | ✅ | ✅ |
| 18 | Data_Quality | Weekly | Feature completeness per ticker, per session | ✅ | ✅ |
| 19 | Promotion_Kill | Weekly | Setup promotion/demotion/kill scoreboard | ✅ | ✅ |
| 20 | Tradability_Score | On signal | Pre-entry structural tradability score | ✅ | ✅ |
| 21 | Config_Diff_Log | On change | Configuration diffs with timestamps | ✅ | ✅ |

---

## PHASE 8 — OUROBOROS INTELLIGENCE AUDIT

### Current Ouroboros Capabilities

| Capability | Status | Quality |
|-----------|--------|---------|
| Store trade outcomes | ✅ Working | Good — persistent_memory.json |
| Compute Bayesian WR | ✅ Working | Good — Laplace smoothing |
| Optimize Kelly | ✅ Working | Adequate — drift guardrails |
| Adjust Chandelier ATR | ✅ Working | Adequate — single multiplier |
| Regime accuracy check | ✅ Working | Basic — Hurst prediction vs outcome |
| Per-ticker stats | ✅ Working | Good — cumulative + rolling 90d |
| Auto-generate lessons | ✅ Working | Basic — avoid/strong ticker only |
| Indicator intelligence | ✅ Working | Good — threshold discovery with lift |
| Ticker blacklist | ⚠️ Written | NOT checked in bridge |
| Cost-aware learning | ❌ Missing | N0 added fields, learning not updated |
| Trade taxonomy | ❌ Missing | No classification beyond W/L |
| Missed winner analysis | ❌ Missing | No counterfactual tracking |
| Setup class benchmarks | ❌ Missing | No per-class performance |
| Anomaly clustering | ❌ Missing | No anomaly detection |
| Structural tradability | ❌ Missing | No pre-entry quality scoring |

### BUILD NOW: Cost-Aware Nightly Learning

The most critical fix is making nightly_v6.py cost-aware. Currently it optimizes gross WR and gross P&L. It needs to:

1. **Compute net WR** = wins where final_pnl > 0 (already uses final_pnl which is net)
2. **Compute net expectancy** = avg(final_pnl) per trade (includes commission)
3. **Identify spread victims** = trades where |final_pnl| < 2 × total_commission
4. **Weight Kelly by net, not gross** = kelly input should use net win/loss averages
5. **Report cost drag** = sum(total_commission) / starting_equity per day

### BUILD NOW: Ticker Blacklist Enforcement

bridge.py needs to check the blacklist before signal generation:

```python
# At top of process_tick(), after warmup check:
_blacklist = _load_ticker_blacklist()
symbol = ticker_symbols.get(ticker_id, "")
if symbol in _blacklist:
    return no_signal_base
```

---

## PHASE 9 — CLAUDE / LLM INTEGRATION ARCHITECTURE

### Claude Integration Decision Matrix

| Role | Path | Priority | Build Now? |
|------|------|----------|-----------|
| **Claude-as-Forensic-Analyst** | COLD: Nightly review of trades | P0 | ✅ YES |
| **Claude-as-Anomaly-Diagnostician** | COLD: Nightly anomaly classification | P0 | ✅ YES |
| **Claude-as-Strategy-Critic** | COLD: Weekly strategy review | P1 | ✅ YES |
| **Claude-as-Operator-Briefer** | COLD: Daily morning briefing | P1 | ✅ YES |
| **Claude-as-Macro-Interpreter** | COLD: Event calendar interpretation | P1 | Design NOW |
| **Claude-as-Ouroboros-Advisor** | COLD: Challenge nightly recommendations | P2 | Design NOW |
| **Claude-as-Code-Reviewer** | COLD: PR review assistance | P2 | Design NOW |
| **Claude-as-PR-Generator** | COLD: Auto-generate PRs from recs | P3 | DEFER |
| **Claude-as-Researcher** | COLD: External research | P3 | DEFER |
| **Real-time trade approval** | HOT: NEVER | — | ❌ REJECTED |
| **Real-time entry timing** | HOT: NEVER | — | ❌ REJECTED |

### Architecture: Claude Nightly Review Module

**BUILD NOW: python_brain/ouroboros/claude_review.py**

```python
"""Claude Nightly Trade Review — COLD PATH only.

Runs after nightly_v6 (04:52 UTC). Reads today's trades, rejected signals,
anomalies, and parameter changes. Produces structured analysis via Claude API.

Output: data/claude_reviews/{date}_review.json
Consumed by: Telegram morning briefing, Google Sheets, operator dashboard.

QUARANTINE RULES:
  - NEVER writes to WAL
  - NEVER modifies live config
  - NEVER influences real-time decisions
  - Read-only analysis of completed-day data
  - Suggestions go to recommendation queue, NOT auto-applied
"""

import json
import os
from pathlib import Path
from datetime import datetime

def build_review_context(wal_trades, rejected_signals, anomalies, params):
    """Build the context package for Claude review."""
    return {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "trades": wal_trades,
        "rejected_signals": rejected_signals[:50],  # Cap to control token usage
        "anomalies": anomalies,
        "parameter_changes": params,
        "system_prompt": REVIEW_SYSTEM_PROMPT,
    }

REVIEW_SYSTEM_PROMPT = """You are an institutional post-trade forensics analyst
reviewing today's trading activity for the AEGIS V2 autonomous trading engine.

Your job is to:
1. Classify each trade: clean winner, grind winner, spread victim, thesis failure, etc.
2. Identify patterns across today's losers (common indicators, session, regime)
3. Flag any anomalies (unusual spread, flash crash, correlation break)
4. Evaluate rejected signals: were any likely missed winners?
5. Suggest ONE concrete parameter adjustment (with confidence and reasoning)
6. Rate today's overall system performance 1-10

Output JSON with keys: trade_classifications, loser_patterns, anomalies,
missed_winner_candidates, parameter_suggestion, overall_rating, narrative.

RULES:
- Be brutally honest. Don't praise the system for lucky wins.
- Focus on what can be FIXED, not what went right.
- A spread victim is not the system's fault — it's a filter improvement opportunity.
- A thesis failure IS the system's fault — the entry logic was wrong.
- NEVER suggest changes to hot-path execution logic.
"""
```

### What Must NEVER Be Autonomous

1. **Order submission** — deterministic only, no LLM in loop
2. **Risk regime changes** — deterministic only
3. **Position sizing** — Kelly formula only
4. **Stop price computation** — Chandelier math only
5. **Live config deployment** — human approval required
6. **Account credentials** — human only
7. **Going live** — IS_LIVE toggle is human-only

---

## PHASE 10 — EXECUTION BACKLOG

### Priority Execution Sequence

| ID | Item | Days | Status | Depends On |
|----|------|------|--------|-----------|
| **N0** | Trade cap, config fix, confidence floor, edge gate, cost WAL | 0 | ✅ DEPLOYED | — |
| **N1a** | Cost-aware nightly learning (net WR, net expectancy, spread victim ID) | 2 | BUILD NOW | N0 |
| **N1b** | Trade taxonomy classifier (10 outcome classes) | 1 | BUILD NOW | N0 |
| **N1c** | Ticker blacklist enforcement in bridge.py | 0.5 | BUILD NOW | N0 |
| **N2a** | SignalRejected WAL event type | 1 | BUILD NOW | N0 |
| **N2b** | Enriched PositionClosed fields (hold_time, session_phase, vix, benchmark) | 1 | BUILD NOW | N0 |
| **N2c** | MissedWinnerCandidate WAL event (1h deferred write) | 1 | BUILD NOW | N2a |
| **N3a** | Structural tradability score (pre-entry quality) | 1 | BUILD NOW | N1b |
| **N3b** | Gate calibration from gate_vetoes.ndjson analysis | 1 | VERIFY LATER | 100+ trades |
| **N4a** | Google Sheets 21-tab architecture (schema + sync) | 2 | BUILD NOW | N2b |
| **N4b** | Win/Loss indicator delta tab | 1 | BUILD NOW | N4a |
| **N5a** | UK holidays enforcement in engine | 0.5 | BUILD NOW | — |
| **N5b** | Bar history persistence (Redis warm-start) | 1 | BUILD NOW | — |
| **N5c** | Bridge SIGHUP hot-reload | 1 | BUILD NOW | — |
| **N6a** | Claude nightly review module | 2 | BUILD NOW | N1b |
| **N6b** | Claude operator morning briefing | 1 | BUILD NOW | N6a |
| **N7a** | Top-100 ticker backfill foundation | 3 | BUILD NOW | — |
| **N7b** | Config diff rollback ledger | 1 | BUILD NOW | — |
| **N8a** | Promotion/demotion/kill scoreboard | 1 | BUILD NOW | N1b, N3a |
| **N8b** | Friction-adjusted expectancy tracking | 1 | BUILD NOW | N2b |
| **N9a** | Macro event backfill layer (economic calendar) | 2 | BUILD NOW | — |
| **N9b** | Event calendar veto logic (FOMC/CPI/NFP suppression) | 1 | BUILD NOW | N9a |
| **GATE** | 100+ trades validation | 35 days | VALIDATE | All N items |

**Total build: 25 days (parallelizable to ~16). Total to live: ~10 weeks.**

---

## PHASE 11 — ADVERSARIAL RED-TEAM REVIEW

### CTO Attack: "The Python bridge is a SPOF."
**Valid.** If Python subprocess crashes, engine falls back to default confidence (65%) but doesn't halt. Bridge has 10-error threshold before respawn. However, there's no alerting on consecutive Python errors.
**Fix:** Add Telegram alert on 5+ consecutive Python errors. BUILD NOW.

### CRO Attack: "PAPER VALIDATION overrides will cause live blowup."
**Valid.** 15 positions at 50% heat is suicidal for live trading. Manual revert is error-prone.
**Fix:** Create config.live.toml with production values. Add startup assertion: if IS_LIVE && positions > 3, abort. BUILD NOW.

### Quant Attack: "VanguardSniper has no backtest. You're trading a hypothesis."
**Valid.** Zero historical validation of the momentum strategy. Only 20 live trades.
**Fix:** Cannot backtest without bar history (need N7a backfill). Mark as VERIFY LATER with 100-trade gate.

### Head of Execution Attack: "LSE ETP confidence boost (+20) is arbitrary."
**Valid.** The +20 confidence boost for LSE leveraged ETPs during LSE hours (bridge.py:886) has no empirical basis. It could cause overtrading of LSE instruments.
**Fix:** Track win rate WITH and WITHOUT boost. Demote if not proven. VERIFY LATER.

### Portfolio Manager Attack: "At £10K, 3 trades/day with 50bps cost is economically marginal."
**Valid.** 3 trades × 252 days × £10 cost = £7,560/year on £10K. That's 76% drag. Even at 1 trade/day, it's 25% drag.
**Fix:** This is already addressed by N0 (max 3 trades/day) and min-edge gate (0.15%). The real fix is selectivity: 1-2 high-quality trades/day maximum. The system will self-calibrate as cost-aware learning (N1a) kicks in. VERIFY LATER.

### Red-Team Consensus: Top 5 Changes Required

1. **config.live.toml** with production values and startup assertion (BUILD NOW)
2. **Python bridge health alerting** via Telegram (BUILD NOW)
3. **LSE boost tracking** — measure with/without impact (VERIFY LATER)
4. **Historical backtest of VanguardSniper** (BLOCKED until N7a backfill)
5. **Cost drag daily reporting** in operator briefing (BUILD NOW via N6b)

---

## PHASE 12 — EVIDENCE REGISTER + GOVERNANCE

### Promotion Criteria (Paper → Live)

| Gate | Threshold | Measurement |
|------|-----------|-------------|
| Trade count | ≥ 100 cost-tracked trades | WAL PositionClosed count |
| Net win rate | ≥ 50% (after commission + spread) | final_pnl > 0 / total |
| Net profit factor | ≥ 1.3 | sum(winners) / sum(|losers|) |
| Max drawdown | < 10% of equity | peak equity - trough equity |
| Cost drag | < 40% of gross P&L | sum(commission) / sum(gross_pnl) |
| Spread victim rate | < 20% of losses | spread_victim count / total_losses |
| Average winner / average loser | > 1.5 | mean(winner_pnl) / mean(|loser_pnl|) |

### Demotion Criteria (Live → Paper)

| Trigger | Action |
|---------|--------|
| 3 consecutive losing days | Reduce to 1 trade/day for 5 days |
| Weekly drawdown > 5% | Halt new entries for 24h |
| Peak drawdown > 12% | Full halt, paper mode, manual review |
| Net WR drops below 40% (rolling 50 trades) | Reduce to 50% sizing for 2 weeks |

### Kill Criteria (Strategy Retirement)

| Trigger | Action |
|---------|--------|
| Net WR < 35% over 100+ trades | Kill strategy, blacklist from Ouroboros |
| Profit factor < 1.0 over 100+ trades | Kill strategy |
| Spread victim rate > 40% | Kill for that instrument class |
| Every trade is a grind or lucky winner | Investigate — may be surviving on noise |

### Rollback Criteria

| Change Type | Rollback Trigger | Rollback Method |
|-------------|-----------------|-----------------|
| Ouroboros parameter change | WR drops 15%+ in 20 trades | Revert dynamic_weights.toml from config_diff_log |
| Gate threshold change | Rejection rate jumps 50%+ with no WR improvement | Revert from config_diff_log |
| New strategy activation | 10 trades with < 30% WR | Disable in strategies.toml |
| Claude recommendation applied | Net negative impact over 20 trades | Revert and flag recommendation as harmful |

### Evidence Register Status

| Item | Status | Proof |
|------|--------|-------|
| WAL event sourcing | ✅ PROVEN | 19 types, CRC32, fsync, replay tests |
| Risk Arbiter 31 checks | ✅ PROVEN | 30 unit tests, 95% coverage |
| Chandelier 5-rung | ✅ PROVEN | 32 tests, rung persistence, collision resolution |
| Ouroboros learning loop | ✅ PROVEN | nightly_v6 → config_writer → SIGHUP |
| N0 Survival Stack | ✅ PROVEN | Deployed 2026-03-20, commit 8c50a66 |
| Cost-aware learning | ❌ NOT YET | N1a — BUILD NOW |
| Trade taxonomy | ❌ NOT YET | N1b — BUILD NOW |
| Missed winner tracking | ❌ NOT YET | N2c — BUILD NOW |
| Claude integration | ❌ NOT YET | N6a — BUILD NOW |
| Historical backtest | ❌ BLOCKED | Requires N7a backfill |
| Live profitability | ❌ NOT YET | Requires GATE (100+ trades) |

---

## STOP-STATE HANDOFF

| Phase | Name | Status | % Complete | Notes |
|-------|------|--------|------------|-------|
| 0 | Ingestion Mandate | COMPLETE | 100% | All 130+ files ingested |
| 1 | Executive Truth | COMPLETE | 100% | Honest verdict delivered |
| 2 | What The System Actually Is | COMPLETE | 100% | Architecture reverse-engineered |
| 3 | End-to-End Trade Lifecycle | COMPLETE | 100% | Full QQQ3.L trace |
| 4 | Honest System Quality Review | COMPLETE | 100% | Grades assigned, findings documented |
| 5 | Logging / Forensic Telemetry | COMPLETE | 100% | Gaps identified, schemas designed |
| 6 | Indicator Intelligence | COMPLETE | 100% | Taxonomy designed, tradability score built |
| 7 | Dashboard / Reporting | COMPLETE | 100% | 21-tab architecture defined |
| 8 | Ouroboros Intelligence | COMPLETE | 100% | Gaps found, cost-aware learning designed |
| 9 | Claude / LLM Integration | COMPLETE | 100% | Architecture defined, roles assigned |
| 10 | Master Plan + Artifacts | COMPLETE | 100% | Backlog written, execution sequence defined |
| 11 | Adversarial Red-Team | COMPLETE | 100% | 5 attacks, 5 fixes |
| 12 | Evidence + Governance | COMPLETE | 100% | Promotion/demotion/kill/rollback criteria |

---

**END OF IMPLEMENTATION MASTER PLAN v6.0**
