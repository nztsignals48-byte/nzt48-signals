# NZT-48 AEGIS V2 — Complete System Summary

## Reverse-Engineered Architecture Audit & Technical Reference

*Document produced by reverse-engineering ~35,000 lines of Rust + ~80,000 lines of Python.*
*For: Technical reader wanting to understand EXACTLY what this system does and how.*

---

## TABLE OF CONTENTS

1. [What Is This?](#1-what-is-this)
2. [The Big Picture — Architecture Diagram](#2-the-big-picture)
3. [Data Flow — From Market Tick to Trade Execution](#3-data-flow)
4. [The Rust Core — Hot Path (Real-Time)](#4-the-rust-core)
5. [The Python Brain — Signal Generation](#5-the-python-brain)
6. [AI Layer — Claude & Gemini Integration](#6-ai-layer)
7. [Mathematical Models — All the Maths](#7-mathematical-models)
8. [Risk Management — The 33-Check Gate](#8-risk-management)
9. [Exit Strategy — Chandelier Trailing Stops](#9-exit-strategy)
10. [The 13 Trading Strategies](#10-trading-strategies)
11. [Instrument Universe — What It Actually Trades](#11-instrument-universe)
12. [Position Sizing — Kelly Criterion Pipeline](#12-position-sizing)
13. [The Self-Improving Loop — Ouroboros Pipeline](#13-ouroboros-pipeline)
14. [Configuration & Deployment](#14-configuration)
15. [Key Design Principles](#15-design-principles)
16. [Glossary](#16-glossary)

---

## 1. WHAT IS THIS?

**NZT-48 AEGIS V2** is an automated algorithmic trading engine designed to trade **global equities and leveraged ETPs** inside a **UK ISA (Individual Savings Account)** with a £10,000 starting capital. It uses a **two-tier AI architecture**: a deterministic Rust execution engine (hot path) plus Claude (Anthropic) and Gemini (Google) on the cold path for nightly analysis and universe curation.

### The Elevator Pitch

> A Rust trading engine connects to Interactive Brokers (IBKR), receives real-time
> market data across 14 exchanges (~4,600 contracts, 36K+ discovery universe) and
> ~22 hours/day. It generates entry signals via 7 Python strategy systems (S1-S7),
> passes every potential trade through 33 sequential risk checks, manages open
> positions with an adaptive trailing-stop system, and nightly recalibrates all its
> own parameters using a combination of statistical analysis (Ouroboros pipeline),
> Claude AI forensic trade review, and Gemini-driven universe curation.

### Key Facts

| Attribute | Value |
|-----------|-------|
| **Language** | Rust (hot path) + Python (cold path) |
| **AI Layer** | Claude (Anthropic) — nightly forensics + Gemini (Google) — universe curation |
| **Rust LOC** | ~35,000 lines |
| **Python LOC** | ~80,000 lines |
| **Account Type** | UK Stocks & Shares ISA |
| **Starting Capital** | £10,000 |
| **Annual ISA Limit** | £20,000 |
| **Live Contracts** | ~4,600 across 14 exchanges |
| **Discovery Universe** | 36,000+ tickers (research/backtesting) |
| **Instruments** | Global equities (US, UK, EU, Asia) + leveraged/inverse ETPs (3x, 5x) on LSE |
| **Exchanges** | LSE, LSEETF, SMART (US), TSE, HKEX, XETRA, Euronext, KRX, ASX, SGX + more |
| **Trading Hours** | ~22 hours/day (23:00–21:00 London, with a 2-hour "Dark" maintenance window) |
| **Broker** | Interactive Brokers (IB Gateway API) |
| **Current Mode** | Paper trading (live mode structurally ready) |
| **Deployment Target** | AWS EC2 (t3.medium, Amazon Linux 2) |

### What are Leveraged ETPs?

Leveraged ETPs amplify daily returns of an underlying index:

```
If QQQ (Nasdaq 100 ETF) moves +1% today:
  → QQQ3.L (3x Leveraged ETP on LSE) moves approximately +3%
  → QQQ5.L (5x Leveraged ETP on LSE) moves approximately +5%

If QQQ moves -1% today:
  → QQQ3.L moves approximately -3%
  → QQQ5.L moves approximately -5%
```

This leverage amplifies both gains AND losses, making risk management critical.

### What is an ISA?

A UK ISA is a tax-free savings/investment wrapper:
- **No capital gains tax** on profits
- **No income tax** on dividends
- **£20,000 annual deposit limit** (2025/26 tax year)
- **No short selling allowed** (long only)
- **Certain exchanges blocked** (China, India, Taiwan direct)

---

## 2. THE BIG PICTURE

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        INTERACTIVE BROKERS (IB Gateway)                 │
│                     Market Data + Order Execution API                   │
└───────────────┬─────────────────────────────────────┬───────────────────┘
                │ Real-time ticks (100ms polling)      │ Order submit/fill
                ▼                                      ▲
┌─══════════════════════════════════════════════════════════════════════──┐
│  ╔═══════════════════════ RUST ENGINE (Hot Path) ═══════════════════╗  │
│  ║                                                                  ║  │
│  ║  ┌──────────────┐    ┌────────────────┐    ┌─────────────────┐  ║  │
│  ║  │  Universe     │───▶│  Python Brain  │───▶│  Risk Arbiter   │  ║  │
│  ║  │  Filter       │    │  (subprocess)  │    │  (33 checks)    │  ║  │
│  ║  │              │    │  Signal gen    │    │  Fail-closed    │  ║  │
│  ║  │  Vanguard ──▶│    │  S1-S7 + VS    │    │                 │  ║  │
│  ║  │  Apex    ──▶│    │  Kelly sizing  │    │  APPROVE ──────▶│  ║  │
│  ║  │  Filter  ──X│    │  Indicators    │    │  or REJECT      │  ║  │
│  ║  └──────────────┘    └────────────────┘    └────────┬────────┘  ║  │
│  ║                                                      │           ║  │
│  ║          ┌───────────────────────────────────────────┘           ║  │
│  ║          ▼                                                       ║  │
│  ║  ┌──────────────────┐    ┌──────────────────────────────────┐   ║  │
│  ║  │  WAL Writer      │    │  Exit Engine                     │   ║  │
│  ║  │  (Write-Ahead    │    │  Chandelier 5-Rung Trailing Stop │   ║  │
│  ║  │   Log, crash     │    │  + 8 Adaptive Multipliers        │   ║  │
│  ║  │   recovery)      │    │  + Gap Protection                │   ║  │
│  ║  └──────────────────┘    └──────────────────────────────────┘   ║  │
│  ║                                                                  ║  │
│  ║  ┌──────────────────┐    ┌──────────────────────────────────┐   ║  │
│  ║  │  Portfolio State  │    │  Subsystems:                     │   ║  │
│  ║  │  (positions, PnL, │    │  • GARCH(1,1) volatility        │   ║  │
│  ║  │   equity, ISA     │    │  • EVT tail risk (CVaR)         │   ║  │
│  ║  │   tracking)       │    │  • Student-t Kalman filter      │   ║  │
│  ║  │                   │    │  • Hayashi-Yoshida correlation   │   ║  │
│  ║  └──────────────────┘    │  • Thompson sampling (MAB)       │   ║  │
│  ║                           │  • Macro regime (VIX/DXY/credit) │   ║  │
│  ║                           │  • FX rate table (multi-currency) │   ║  │
│  ║                           └──────────────────────────────────┘   ║  │
│  ╚══════════════════════════════════════════════════════════════════╝  │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ WAL journal (ndjson)
                               ▼
┌─══════════════════════════════════════════════════════════════════════──┐
│  ╔═══════════════════ PYTHON OUROBOROS (Cold Path) ═════════════════╗  │
│  ║  Runs NIGHTLY outside trading hours (never touches live state)   ║  │
│  ║                                                                  ║  │
│  ║  Step 0: GARCH Calibration (fit ω, α, β parameters)            ║  │
│  ║  Step 1: Timing Guard (refuse if LSE open)                      ║  │
│  ║  Step 2: Cold Start Safety (days 1-3: conservative defaults)    ║  │
│  ║  Step 3: Ingest WAL journal (read-only!)                        ║  │
│  ║  Step 4: Bayesian Win Rate (Laplace-smoothed)                   ║  │
│  ║  Step 5: Deflated Sharpe Ratio (overfitting detection)          ║  │
│  ║  Step 6: Kelly Accelerator (per-ticker optimal fraction)        ║  │
│  ║  Step 7: Exit Calibration (MAE/MFE → Chandelier tuning)        ║  │
│  ║  Step 8: Regime Hunting (classify market conditions)            ║  │
│  ║  Step 9: Alpha Sieve (promote/demote/lock tickers)              ║  │
│  ║  Step 10: Output (dynamic_weights.toml, universe_classification)║  │
│  ╚══════════════════════════════════════════════════════════════════╝  │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ SIGHUP (hot-reload signal)
                               ▼
                     Rust engine reloads parameters

┌─══════════════════════════════════════════════════════════════════════──┐
│  ╔═══════════════════ AI LAYER (Cold Path) ════════════════════════╗  │
│  ║  Claude (Anthropic) — Forensic Analysis & Strategy Discovery    ║  │
│  ║  ─────────────────────────────────────────────────────────────  ║  │
│  ║  • D-NIGHTLY: Forensic trade review (Opus 4.6, daily)          ║  │
│  ║  • D-REGIME: Regime interpretation challenge (Sonnet, 4x/day)  ║  │
│  ║  • D-PARAM: Parameter tuning proposals (Opus, daily)           ║  │
│  ║  • D-HYPOTHESIS: New strategy discovery (Opus, weekly)         ║  │
│  ║  • D-BRIEFING: Morning/evening operator briefings (Sonnet)     ║  │
│  ║  • D-FORENSIC: Per-trade win/loss classification (W1-W5/L1-L7)║  │
│  ║  • 15 specialized modules, FULLY AUTONOMOUS                    ║  │
│  ║  • gate_tuning auto-applies within hard bounds (no human gate) ║  │
│  ║  • Hot-path: curator soft-gate on signals ≥55 confidence       ║  │
│  ║                                                                 ║  │
│  ║  Gemini (Google 2.5-Pro) — Universe + Strategy Weights         ║  │
│  ║  ─────────────────────────────────────────────────────────────  ║  │
│  ║  • Core universe: 80 tickers every 2h → watchlist → Rust       ║  │
│  ║  • Dark horses: 20 movers every 15min → SIGHUP hot-reload     ║  │
│  ║  • Morning brief: avoid/focus tickers + strategy weight seeds  ║  │
│  ║  • Deterministic fallback if API unavailable                   ║  │
│  ║                                                                 ║  │
│  ║  SAFETY: Hard bounds on all parameters. 33-check Rust arbiter  ║  │
│  ║  is final authority. AI tunes parameters, never executes.       ║  │
│  ╚═════════════════════════════════════════════════════════════════╝  │
└──────────────────────────────────────────────────────────────────────┘
```

### The Three Layers

| | **Hot Path (Rust)** | **Python Brain** | **AI Layer (Claude/Gemini)** |
|---|---|---|---|
| **When** | Real-time, 100ms loop | Real-time, subprocess | Nightly/scheduled |
| **Latency** | <1ms per tick | ~10ms per signal | Seconds–minutes |
| **Authority** | FINAL — execution | ADVISORY — signals | AUTONOMOUS — tunes params within hard bounds |
| **Can trade** | Yes — submits to IBKR | No — returns signals | No — writes analysis |
| **Language** | Rust (zero GC) | Python (NumPy) | Claude CLI / Gemini SDK |

---

## 3. DATA FLOW

### Complete Tick-to-Trade Pipeline

```
                              Time: ~100ms per cycle
                              ══════════════════════

  IBKR Gateway           Rust Engine                    IBKR Gateway
  ────────────           ───────────                    ────────────
  Market tick ──────────▶ 1. Poll ticks
  (bid, ask,              2. Universe filter ─────────▶ FILTERED (drop)
   last, vol)                  │
                          Vanguard path ──▶ 3. Python Brain (signal?)
                               │                    │
                               │              ◄─────┘
                          4. RiskArbiter (33 checks)
                               │
                          REJECTED ─────────────────▶ WAL (log rejection)
                               │
                          APPROVED ─────────────────▶ 5. WAL (log intent)
                               │
                          6. Submit order ───────────▶ Buy 15 shares QQQ3.L
                               │                          @ £42.50 LIMIT
                          7. Wait for fill
                               │
                          8. Register exit ──────────▶ Chandelier stop
                               │                       tracking begins
                          9. Monitor position
                               │
                          10. Stop triggered ─────────▶ Sell 15 shares
                                                         @ £41.30 LIMIT
```

### Universe Routing

Every incoming tick is classified before processing:

```
                    ┌─ Vanguard (Tier 1/2) ─── Continuous processing
  Raw Tick ────────▶│                           Every tick → Python Brain
                    ├─ Apex (Tier 3)      ─── 60-second OHLCV snapshots
                    │                           Aggregated → ApexScout
                    └─ FILTERED            ─── Dropped (reasons below)

  Filter Reasons:
    • AmihudIlliquid    — Not enough market depth
    • ErroneousTick     — >15% deviation from 1s EMA (accommodates 3x leverage)
    • ReverseSplit      — >500% overnight gap
    • SyntheticHalt     — No ticks for 30 seconds
    • InvalidTick       — NaN, negative, crossed bid/ask
```

---

## 4. THE RUST CORE

### Module Map (~35,000 lines across 75+ files)

```
rust_core/src/
├── main.rs                 ← Binary entry point, event loop
├── lib.rs                  ← Module declarations
├── engine.rs               ← Core engine: startup, tick processing, shutdown
├── risk_arbiter.rs         ← 33-check risk gate (THE critical file)
├── exit_engine.rs          ← Chandelier trailing stops + priority cascade
├── entry_engine.rs         ← Entry type classification + tier routing
├── position_sizer.rs       ← Kelly criterion + tier-based sizing
├── portfolio.rs            ← Position tracking, equity, PnL
│
├── types/
│   ├── enums.rs            ← TickerId, OrderId, RiskRegime, VetoReason (30+ variants)
│   ├── structs.rs          ← MarketTick, PositionState, RiskDecision
│   ├── execution.rs        ← Order lifecycle types
│   └── wal.rs              ← WAL payload types (12 event types)
│
├── broker.rs               ← BrokerAdapter trait (submit, cancel, reconcile)
├── ibkr_broker.rs          ← IBKR implementation (IB Gateway API)
├── paper_broker.rs         ← Paper trading simulator
│
├── garch_inference.rs      ← GARCH(1,1) real-time volatility
├── garch_evt.rs            ← Extreme Value Theory (tail risk)
├── student_t_kalman.rs     ← Robust Kalman filter (price smoothing)
├── hayashi_yoshida.rs      ← Asynchronous correlation estimator
├── multiframe_vol.rs       ← Multi-timeframe volatility consensus
├── log_thompson_sampler.rs ← Multi-armed bandit (ticker rotation)
│
├── cross_asset_macro.rs    ← Macro regime: VIX, DXY, credit, Fear&Greed
├── regime_detector.rs      ← Market regime classification
├── scanner.rs              ← HotScanner (vol-momentum), RotationScanner
├── predictive_scoring.rs   ← Per-ticker IC tracking, auto-lock
├── quote_imbalance.rs      ← Order book imbalance detection
│
├── universe.rs             ← Ticker classification & routing
├── clock.rs                ← Trading modes (ModeA/B/B+/C/Dark), BST handling
├── session_manager.rs      ← Session boundary management
├── market_scheduler.rs     ← Per-exchange open/close times
├── market_config.rs        ← Exchange-specific parameters
│
├── isa_gate.rs             ← ISA compliance (exchange blocking, £20K limit)
├── liquidation_defense.rs  ← Circuit breakers (drawdown, consecutive losses)
├── smart_router.rs         ← Order routing (limit type selection)
├── split_handler.rs        ← Reverse split / corporate action detection
├── overnight_carry.rs      ← Position carry across sessions (frozen stops)
│
├── python_bridge.rs        ← Subprocess IPC to Python Brain (JSON lines)
├── python_subprocess_manager.rs ← Respawn, crash detection, fork bomb guard
│
├── wal_writer.rs           ← Write-Ahead Log (crash recovery)
├── wal_actor.rs            ← WAL state management
├── wal_compressor.rs       ← WAL file rotation & cleanup
├── wal_replay.rs           ← Startup WAL replay (position recovery)
│
├── config.rs               ← All configuration structs
├── config_loader.rs        ← TOML parsing + validation
├── ouroboros_loader.rs      ← Load nightly calibration artifacts
│
├── telemetry.rs            ← Metrics (ticks, signals, fills, latency)
├── latency_profiler.rs     ← Per-stage pipeline timing
├── hardening.rs            ← Circuit breakers, watchdog, panic guard
├── broker_resilience.rs    ← Broker health monitoring
├── reconciler.rs           ← Position reconciliation (engine vs broker)
├── channel.rs              ← Backpressure-monitored tick channel
├── currency.rs             ← FX rate table (GBP↔USD/JPY/HKD/EUR/SGD)
├── exchange_profile.rs     ← Exchange metadata registry
├── subscription_manager.rs ← IBKR market data line management
└── state_checkpoint.rs     ← Hourly state snapshots
```

### The Main Event Loop (Simplified)

```rust
// main.rs — runs at 100ms intervals (10 Hz)
loop {
    // 1. Check kill switch files (/app/data/KILL, /app/data/PAUSE)
    // 2. Attempt broker reconnection if disconnected (60s intervals)
    // 3. Daily reset check (new trading day?)
    // 4. Hot-reload config on SIGHUP signal
    // 5. Poll market data ticks (non-blocking)
    // 6. Update bar history (ATR calculation)
    // 7. Respawn Python Brain if dead (with exponential backoff)
    // 8. For each tick:
    //    a. Route through Universe filter
    //    b. Build context for Python Brain
    //    c. Get signal (or no_signal) from Python
    //    d. Process tick + signal through engine
    //       → RiskArbiter evaluates
    //       → ExitEngine updates stops
    //       → Orders submitted if approved
    // 9. Detect regime changes → persist to WAL
    // 10. Process broker events (fills, cancels)
    // 11. Check drawdown velocity (>2% in 1 hour → HALT)
    // 12. Periodic reconciliation (every 5 minutes)
    // 13. Emit telemetry snapshot
    // 14. Sleep until next 100ms cycle

    std::thread::sleep(Duration::from_millis(100));
}
```

---

## 5. THE PYTHON BRAIN

### Signal Generation via Subprocess

The Rust engine spawns a Python subprocess and communicates via JSON lines over stdin/stdout:

```
  Rust Engine                              Python Brain (subprocess)
  ───────────                              ────────────────────────
  {"tick": {...}, "ctx": {...}} ──stdin──▶  Receives tick + context
                                            Runs S1-S7 strategy systems
                                            Computes indicators (RSI, ADX, RVOL,
                                            VPIN, TMR, Hurst, Bollinger, IBS...)
                                            Calculates Kelly fraction
  ◀──stdout── {"signal": {...}}             Returns signal (or null)
```

### What the Python Brain Returns

```python
# A BrainSignal contains:
{
    "direction": "Long",          # Always Long (ISA = no short selling)
    "confidence": 72.5,           # Signal strength [0-100]
    "kelly_fraction": 0.043,      # Position size fraction [0.0-0.20]
    "shares": 15,                 # Calculated share count
    "strategy": "S1_Microstructure", # Which of 7 systems generated this
    "rvol": 1.85,                 # Relative volume (vs 20-day avg)
    "hurst": 0.62,                # Hurst exponent (trend persistence)
    "adx": 34.2,                  # Average Directional Index
    "vol_slope": 0.15,            # Volume slope (5-min regression)
    "vwap_dist_pct": -0.3,        # Distance from VWAP (%)
    "structural_score": 68.0,     # Tradability score [0-100]
    "entry_type": "TypeB",        # Classification label
    "rsi": 42.1,                  # RSI(14) at signal time
    "ibs": 0.23                   # Internal Bar Strength
}
```

---

## 6. AI LAYER — CLAUDE & GEMINI INTEGRATION

### Two-Tier AI Architecture

The system implements a strict separation: **Rust decides, AI advises**.

```
    ┌─────────────────────────────────────────────────────────────────┐
    │                    HOT PATH (Rust)                              │
    │  Deterministic, <1ms, 33 risk checks, ALL execution decisions  │
    │  ZERO AI involvement in real-time trading                      │
    └─────────────────────────────────┬───────────────────────────────┘
                                      │ WAL events (read-only)
                                      ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                    COLD PATH (AI Layer)                         │
    │                                                                 │
    │  ┌─── CLAUDE (Anthropic) ────────────────────────────────────┐ │
    │  │  15 specialized modules via Claude Code CLI (claude -p)    │ │
    │  │  Models: Opus 4.6 (critical), Sonnet (analysis), Haiku    │ │
    │  │  Budget: ~$3-5/day on Max subscription                    │ │
    │  └───────────────────────────────────────────────────────────┘ │
    │                                                                 │
    │  ┌─── GEMINI (Google 2.5-Pro) ───────────────────────────────┐ │
    │  │  Universe curation: 80 core + 20 dark horse tickers       │ │
    │  │  Morning briefs, catalyst detection                       │ │
    │  │  Deterministic fallback if API unavailable                │ │
    │  └───────────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────────────┘
```

### Claude's 15 Decision Types

| Decision | Model | Frequency | Purpose |
|----------|-------|-----------|---------|
| **D-NIGHTLY** | Opus 4.6 | Daily | Forensic trade review — classifies each trade as W1-W5 (winner) or L1-L7 (loser) |
| **D-REGIME** | Sonnet | 4x/day | Challenge the HMM regime classification — agree or disagree? |
| **D-PARAM** | Opus 4.6 | Daily | Propose parameter changes (±10% max) with statistical justification |
| **D-HYPOTHESIS** | Opus 4.6 | Weekly | Generate new trading strategy hypotheses from historical patterns |
| **D-BRIEFING-AM** | Sonnet | Daily | Morning operator briefing: today's regime, focus tickers, key risks |
| **D-BRIEFING-PM** | Sonnet | Daily | Evening wrap: P&L summary, top/worst performers, lessons |
| **D-FORENSIC** | Sonnet | Per-trade | Deep dive on individual trades: entry quality, exit timing, lesson |
| **D-CLUSTER** | Opus 4.6 | Weekly | Root cause analysis of consecutive loss clusters |
| **D-DECAY** | Opus 4.6 | Weekly | Which strategies are losing alpha? Early warning system |
| **D-CONFIG** | Sonnet | Daily | Detect contradictions in parameter combinations |
| **D-UNIVERSE** | Opus 4.6 | Monthly | Expand or contract the tradeable universe |
| **D-POSTMORTEM** | Opus 4.6 | On-event | Post-mortem when a strategy gets killed |
| **D-JOURNAL** | Haiku | Daily | Compress institutional memory into searchable format |
| **D-ERROR** | Sonnet | On-demand | Root cause investigation of anomalies or errors |
| **D-DEPLOY** | Sonnet | Per-deploy | Pre-deploy code diff review — is this safe? |

### Fully Autonomous Operation (No Human in Loop)

The system starts at **0 trades** and self-improves every day. No human approval is required for parameter changes — hard bounds are the safety net:

```
    AUTONOMOUS MODE — Claude recommendations auto-apply within hard bounds:

    Safety Rails (cannot be overridden by any AI):
    ┌──────────────────────────────────────────────────────────┐
    │  Kelly fraction:      [0.10, 0.35], max ±10% per night  │
    │  Chandelier ATR mult: [1.5, 5.0],  max ±15% per night   │
    │  Confidence floor:    [50, 85],    max ±10 pts per night │
    │  Spread veto:         [0.10, 0.80], max ±0.10 per night  │
    │  30-day drift cap:    max 50% total drift from baseline  │
    │  Risk-increasing:     Telegram notified (still applied)  │
    └──────────────────────────────────────────────────────────┘

    Day 1 (0 trades): Gemini seeds strategy weights, Claude reviews = empty
    Day 7 (5 trades): Claude starts seeing patterns, first gate_tuning
    Day 30 (20+ trades): Statistical signal emerges, Kelly ramps
    Day 90 (50+ trades): Full self-tuning loop with meaningful data

    Every night the system gets smarter:
      Ouroboros recalculates Bayesian WR + Kelly + regime scales
      → Claude reviews and proposes gate_tuning
      → approval_gate auto-applies within hard bounds
      → SIGHUP → Rust engine picks up new parameters
      → Tomorrow's trades use today's lessons
```

### Claude's Trade Classification Taxonomy

Every completed trade is classified:

```
    WINNERS:                              LOSERS:
    W1 — Clean Trend (rode momentum)     L1 — Spread Victim (slippage > edge)
    W2 — Grind (slow accumulation)       L2 — Stop Hunted (stopped, then reversed)
    W3 — Rung Climber (hit 3+ rungs)     L3 — Late Entry (move already played out)
    W4 — VWAP Reclaim (bounced VWAP)     L4 — Macro Crush (overnight event)
    W5 — Macro Surf (rode catalyst)      L5 — Regime Mismatch (wrong market)
                                          L6 — Fake Breakout (instant reversal)
                                          L7 — Time Decay (lost to theta/carry)
```

### Gemini's Universe Curation

Gemini (Google 2.5-Pro) selects which tickers the engine actively streams:

```
    Every 2 hours:  scan_core_universe()  → 80 tickers for real-time streaming
    Every 15 min:   scan_dark_horses()    → 20 unusual movers for rotation
    Daily 06:00:    morning_brief()       → Pre-market regime + strategy weights

    3-Source Allocation:
    ┌─── Memory Winners (40 slots) ─── Recurring profitable tickers
    ├─── IBKR Scanner (20 slots)   ─── High-RVOL from IBKR's own scanner
    └─── Gemini AI (20 slots)      ─── Catalyst/sector-aware selection

    Fallback: If Gemini API fails → deterministic sort by volume × ATR × leverage
```

### What's Actually Wired (Live Execution Paths)

```
    WIRED (affects live trading):
    ┌─────────────────────────────────────────────────────────────────────────┐
    │  Claude curator (bridge.py)     — Soft-gate on every signal ≥55 conf  │
    │                                    Can reduce confidence 15pts / halve │
    │                                    Kelly. Fallback: 10% haircut.       │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Gemini core universe (cron)    — Every 2h → ticker_selector merges   │
    │                                    → active_watchlist.json → Rust      │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Gemini dark horses (cron)      — Every 15min → merged into watchlist │
    │                                    as Apex tier → SIGHUP hot-reload    │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Gemini morning brief (06:00)   — avoid_tickers → blacklist merged    │
    │                                    strategy_weights → dynamic_weights  │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Claude review → approval_gate  — gate_tuning auto-applied within     │
    │                                    hard bounds. No human approval.     │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Gemini strategy weights (seed) — Morning brief weights seed bridge   │
    │                                    allocation until live P&L overrides │
    └─────────────────────────────────────────────────────────────────────────┘

    COLD PATH ONLY (informational, no live impact):
    • Claude briefings (AM/PM)  → Telegram only, engine ignores
    • Claude forensic reviews   → Written to disk for operator to read
    • Claude psych audit        → Weekly bias detection report
```

### Quarantine Enforcement

```
    Claude writes to:  /app/data/claude/ (reviews, briefings, challenges)
    Gemini writes to:  /app/data/gemini/ (core_universe, dark_horses, brief)

    Feedback loops: approval_gate reads Claude reviews + applies within hard bounds.
    ticker_selector reads Gemini dark horses + merges into watchlist.
    config_writer reads Gemini morning brief + merges avoid/focus/weights.

    CANNOT: submit orders, cancel orders, override risk arbiter, modify stops.
```

### Bullish Bias Correction

Every Claude prompt includes explicit debiasing:

> "IMPORTANT: You have a documented BULLISH BIAS (Book 198). Actively correct
> for this by giving EXTRA WEIGHT to bearish evidence."

### Signal Validation Pipeline (Books 207, 208, 209)

Every signal passes through 3 gates before reaching Rust:

```
    Signal from strategy
          ↓
    ┌─── Gate 1: Quality Gate (Book 208) ────────────────────────────┐
    │  Strategy lifecycle: PAPER → VALIDATED → LIVE → SUSPENDED       │
    │  PAPER strategies: signals logged to shadow_signals.ndjson      │
    │  SUSPENDED/RETIRED: signals blocked entirely                    │
    │  Unknown strategies default to LIVE (no disruption)             │
    │  Compounding Machine auto-kill → SUSPENDED + Telegram alert     │
    └──────────────────────────────────────────────────┬──────────────┘
                                                       ↓
    ┌─── Gate 2: Schema Validation (Book 207) ───────────────────────┐
    │  NormalizedSignal dataclass validates every field:               │
    │    direction ∈ {Long, Short}                                    │
    │    confidence [0-100], kelly [0.0-0.35], shares ≥ 0             │
    │    NaN/Inf → None (prevents JSON serialization errors)          │
    │  All extra fields (rsi, vpin, entry_type) passed through        │
    └──────────────────────────────────────────────────┬──────────────┘
                                                       ↓
    ┌─── Bayesian Aggregation (Book 209) ────────────────────────────┐
    │  When 2+ strategies fire on same tick:                          │
    │    Posterior from all sources adjusts best signal confidence     │
    │    Consensus → boost up to +10 confidence                       │
    │    Conflict → penalty up to -15 confidence                      │
    │  Source calibration: confusion matrix per source, persisted      │
    │  LR = 1.0 until 10+ observations (no harm on day 1)            │
    └────────────────────────────────────────────────────────────────┘
```

### Escalation Protocol (Book 58)

Telegram alerts escalate automatically if unacknowledged:

```
    WARNING alert sent
          ↓ 15 min unacked
    CRITICAL (Telegram countdown: "AUTO-FLATTEN IN X MIN")
          ↓ repeated every 5 min
          ↓ 60 min total unacked
    EMERGENCY → write /app/data/KILL → Rust flattens + shuts down

    Operator commands (Telegram):
      /ack        — Acknowledge oldest pending alert
      /ack <id>   — Acknowledge specific alert
      /ack-all    — Acknowledge all pending alerts
      /alerts     — Show pending alerts with age
```

---

## 7. MATHEMATICAL MODELS

### 7.1 Kelly Criterion (Position Sizing)

The Kelly Criterion determines what fraction of your bankroll to risk per trade.

**Formula:**

```
                    p × b  −  (1 − p)
        f*  =  ─────────────────────
                        b

Where:
    f* = Optimal fraction of capital to bet
    p  = Probability of winning (Bayesian win rate)
    b  = Win/Loss ratio (avg_win / avg_loss)
```

**Example:**

```
Given:  Win rate = 40%,  Avg winner = 3%,  Avg loser = 1.5%
        p = 0.40,  b = 3.0/1.5 = 2.0

        f* = (0.40 × 2.0 − 0.60) / 2.0
           = (0.80 − 0.60) / 2.0
           = 0.10  (10% of equity)
```

**AEGIS applies 3 safety layers:**

```
Step 1: Raw Kelly         f* = 0.10  (10%)
Step 2: Half-Kelly cap    f  = 0.10 × 0.50 = 0.05  (5%)
Step 3: Clamp             f  = clamp(0.05, [0.02, 0.05]) = 0.05
Step 4: EWA blend with prior:
        f_final = 0.30 × f_new + 0.70 × f_prior
                = 0.30 × 0.05 + 0.70 × 0.04  (if prior was 4%)
                = 0.015 + 0.028
                = 0.043  (4.3% of equity)

At £10,000 equity → risking £430 per trade
At £42.50/share  → buying 10 shares (£425 notional)
```

### 7.2 GARCH(1,1) Volatility Model

GARCH (Generalized Autoregressive Conditional Heteroskedasticity) forecasts future volatility from past returns.

**The Recursion (runs every tick, O(1) cost):**

```
        σ²ₜ  =  ω  +  α · r²ₜ₋₁  +  β · σ²ₜ₋₁

Where:
    σ²ₜ   = Conditional variance at time t (our forecast)
    ω      = Long-run variance floor (intercept), typically ~1×10⁻⁵
    α      = Shock sensitivity (how much yesterday's surprise matters), typically ~0.10
    β      = Persistence (how much yesterday's volatility matters), typically ~0.85
    r²ₜ₋₁  = Squared return at time t-1 (the "shock")

Constraint: α + β < 1  (ensures stationarity — volatility doesn't explode)
```

**Intuition:** Today's volatility ≈ 85% of yesterday's volatility + 10% of yesterday's shock + a small constant floor.

**How It's Used:**
- Nightly: Python's `arch` library fits (ω, α, β) to 60-day history
- Real-time: Rust incrementally updates σ² with each new return
- CHECK 25: If σ > 0.80% × √leverage → reject trade (too volatile)

### 7.3 EVT — Extreme Value Theory (Tail Risk)

After GARCH standardizes returns, EVT estimates "how bad can a bad day get?"

**The Process:**

```
Step 1: Compute standardized residuals from GARCH
        εₜ = rₜ / σₜ  (actual return ÷ GARCH forecast)

Step 2: Set a threshold u = 90th percentile of losses
        (Focus on the worst 10% of days)

Step 3: Fit a Generalized Pareto Distribution (GPD) to exceedances
        y = -εᵢ - u  (how far past the threshold each bad day went)

Step 4: Estimate GPD parameters:
        ξ = 0.5 × (mean²/variance - 1)    ← Shape (heavy-tailedness)
        σ = mean × (1 - ξ)                 ← Scale

Step 5: Compute CVaR (Conditional Value-at-Risk):
                  VaR_α         σ - ξ·u
        CVaR_α = ────── + ──────────────
                  1 - ξ       1 - ξ
```

**What CVaR Tells You:**

> "Given that we're already having a 1-in-100 bad day, how much should we
> expect to lose on average?"

If CVaR = 8%, that means: on a really bad day (worse than 99% of days), the average loss is 8%.

**Used in:** CHECK 24 — portfolio-level tail risk limit.

### 7.4 Student-t Kalman Filter (Price Smoothing)

A standard Kalman filter assumes Gaussian noise. Financial data has fat tails (sudden spikes). The Student-t variant adds **Huber weighting** to resist outliers:

```
PREDICT:
    x̂ₜ = xₜ₋₁           (state prediction = last estimate)
    P̂ₜ = Pₜ₋₁ + Q        (covariance grows by process noise Q)

UPDATE with Huber robustness:
    innovation = zₜ - x̂ₜ                    (measurement - prediction)
    S = P̂ₜ + R                               (innovation covariance)

    normalized = |innovation| / √S

    IF normalized ≤ δ:
        weight = 1.0                          (normal update)
    ELSE:
        weight = δ / normalized               (DOWNWEIGHT the outlier)

    K = (P̂ₜ / S) × weight                    (Kalman gain, attenuated)
    xₜ = x̂ₜ + K × innovation                (state update)
    Pₜ = P̂ₜ × (1 - K)                        (covariance update)

    δ adapts dynamically:
        δ = 1.345 × MAD(last 200 residuals)   (Median Absolute Deviation)
```

**Why it matters:** Smooths price data for momentum calculations while ignoring flash crashes and erroneous ticks. The `kalman_divergence` (raw price - smoothed price) signals breakouts.

### 7.5 Hayashi-Yoshida Correlation

Standard correlation requires synchronized timestamps. Market ticks arrive at different times for different assets. Hayashi-Yoshida handles this:

```
Traditional correlation: needs prices at SAME timestamps
    t1: AAPL=$150, MSFT=$400  ← need both at t1
    t2: AAPL=$151, MSFT=$401  ← need both at t2

Hayashi-Yoshida: works with OVERLAPPING intervals
    AAPL: [t1,t3] → return_a, [t3,t7] → return_a'
    MSFT: [t2,t5] → return_b, [t5,t8] → return_b'

    If intervals overlap: count their product
    HY_cov = Σᵢⱼ rᵢᵃ × rⱼᵇ × 𝟙{intervals overlap}
    correlation = HY_cov / (√var_a × √var_b)
```

**Used in:** Adaptive stop multipliers, correlation concentration risk check (max 3 correlated positions).

### 7.6 Bayesian Win Rate (Laplace Smoothing)

Raw win rates are unreliable with small samples. Laplace smoothing shrinks toward 50%:

```
                wins + 1
    WR_bayes = ──────────
                total + 2

Examples:
    2 wins / 3 trades:  raw = 66.7%,  Bayesian = 3/5 = 60.0%
    0 wins / 1 trade:   raw =  0.0%,  Bayesian = 1/3 = 33.3%
    8 wins / 10 trades:  raw = 80.0%,  Bayesian = 9/12 = 75.0%
    80 wins / 100 trades: raw = 80.0%, Bayesian = 81/102 = 79.4%
```

The more data you have, the less the smoothing matters. With few trades, it prevents extreme estimates.

### 7.7 Deflated Sharpe Ratio

The standard Sharpe Ratio can be misleading if a strategy has negative skew (rare big losses) or was selected from many backtests (overfitting):

```
    Standard Sharpe:    SR = mean(returns) / std(returns)

    Deflated Sharpe (Bailey & López de Prado, 2014):

                              1 - γ₃·SR₀ + ((γ₄ - 1)/4)·SR₀²
        σ_SR₀ = sqrt( ────────────────────────────────────────── )
                                      T - 1

        DSR = Φ( (SR* - SR₀) / σ_SR₀ )

    Where:
        γ₃ = Skewness (negative = left tail risk)
        γ₄ = Kurtosis (>3 = fat tails)
        T  = Number of trades
        SR₀ = Expected max Sharpe from random strategies
        Φ  = Standard normal CDF
```

**Interpretation:** DSR > 0.95 means the strategy is likely genuinely profitable, not just a lucky backtest. DSR < 0.50 means it could be noise.

### 7.8 Wilder's ATR (Average True Range)

The ATR measures volatility by tracking how much an asset moves per period:

```
    True Range = max(
        High - Low,                     ← Today's range
        |High - Previous Close|,        ← Gap up
        |Low  - Previous Close|         ← Gap down
    )

    ATR = exponential moving average of True Range

    Wilder's smoothing:
        ATR_t = ATR_{t-1} × (n-1)/n + TR_t / n

    Where n = period (typically 14)
```

**Used everywhere:** Stop placement, position sizing, signal filtering, exit trailing distances.

### 7.9 Multi-Frame Volatility

Volatility computed at 5 different timeframes, then combined:

```
    Frame       Annualization Factor        Purpose
    ─────       ────────────────────        ────────
    1-minute    √(252 × 390) = 313.5       Microstructure noise
    5-minute    √(252 × 78)  = 140.2       Intraday regime
    15-minute   √(252 × 26)  = 80.9        Medium-term trend
    60-minute   √(252 × 6.5) = 40.5        Session volatility
    Daily       √252          = 15.87       Strategic level

    Consensus = weighted average (weights ∝ sample count, min 30 samples)
```

### 7.10 Thompson Sampling (Multi-Armed Bandit)

Used to decide which tickers to actively monitor. Like a slot machine problem:

```
    For each ticker (arm):
        Track: log-returns collected so far
        Maintain: posterior mean (μ) and variance (τ²)

    Conjugate normal-normal update:
        posterior_precision = 1.0 + n / sample_variance
        τ² = 1 / posterior_precision
        μ  = τ² × (n / sample_variance) × sample_mean

    Selection:
        Sample from each arm's posterior: θᵢ ~ N(μᵢ, τ²ᵢ)
        Select top-k arms by sampled value

    Exploration-exploitation balance:
        High uncertainty (τ²) → more likely to be sampled (explore)
        High mean (μ) → more likely to be sampled (exploit)
```

---

## 8. RISK MANAGEMENT — THE 33-CHECK GATE

The RiskArbiter is a **synchronous, fail-closed** gate. Every potential trade must pass ALL 33 checks. Any single failure → REJECT.

### Risk Regime Hierarchy

```
    NORMAL  ─────▶  REDUCE  ─────▶  FLATTEN  ─────▶  HALT
    (trade freely)  (half size)    (exits only)    (no activity)

    Transitions are ONE-WAY (escalation only during a session).
    Can only de-escalate via: daily reset, or manual intervention.
```

### The 33 Checks (in execution order)

```
┌─────┬──────────────────────┬──────────────────────────────────────────────────┐
│ CHK │ Category             │ Rule                                             │
├─────┼──────────────────────┼──────────────────────────────────────────────────┤
│  1  │ ISA Safety           │ Short sell → HALT (ISA is long-only)             │
│  2  │ Portfolio            │ Inverse mutual exclusion (can't hold 3x AND      │
│     │                      │ inverse 3x on same underlying)                   │
│  5  │ Regime               │ If HALT or FLATTEN → reject all entries          │
│  6  │ Portfolio            │ Total positions ≥ max (3 in Normal, 1 in Reduce) │
│  7  │ Data Quality         │ Last tick > 120s old → HALT                      │
│  8  │ Infrastructure       │ Broker disconnected → HALT                       │
│  9  │ Infrastructure       │ WAL unavailable → HALT (no crash recovery)       │
│ 10  │ Signal Quality       │ Confidence < floor ÷ √leverage                   │
│ 11  │ Timing               │ After 15:45 London → reject                      │
│ 13  │ Liquidity            │ Bid-ask spread > 0.5% → reject                   │
│ 14  │ Cash Management      │ Cash buffer < 10% of equity → reject             │
│ 15  │ Portfolio Heat       │ Sum of risk > 15% of equity → reject             │
│ 16  │ Sector Concentration │ Sector heat > 33% → reject                       │
│ 17  │ ISA Compliance       │ Would breach £20K annual limit → reject          │
│ 18  │ Drawdown             │ Daily loss > 2% → FLATTEN                        │
│ 19  │ Velocity             │ >5 same-ticker intents in 5 min → reject         │
│ 19b │ System Velocity      │ >10 total intents in 5 min → reject              │
│ 20  │ Macro                │ VIX > 30 or credit spread > 200bps → escalate    │
│ 21  │ Consecutive Losses   │ ≥3 stop-losses in a row → HALT                   │
│ 22  │ Momentum Re-entry    │ Duplicate position gating (IC thresholds)        │
│ 23  │ Ticker Status        │ Halted ticker (reverse split, etc) → reject      │
│ 24  │ Tail Risk            │ CVaR heat > 22.5% → reject                       │
│ 25  │ Volatility           │ GARCH σ > 0.80% × √leverage → reject            │
│ 26  │ Signal Strength      │ Scanner score < 30/100 → reject                  │
│ 27  │ Position Size        │ Kelly fraction < 0.5% → reject (too small)       │
│ 28  │ Cost Control         │ Daily trades ≥ 3 → reject (RT cost = 0.50%)      │
│ 29  │ Edge Requirement     │ Expected edge < spread + commission → reject     │
│ 30  │ Weekly Drawdown      │ Weekly loss > 7% from Monday HWM → FLATTEN       │
│ 31  │ Peak Drawdown        │ All-time loss > 15% from HWM → HALT             │
│ 32  │ Equity Floor         │ Equity < 70% of initial (£7,000) → HALT         │
│ 34  │ Correlation          │ >3 positions in same sector → reject              │
│ 35  │ Tradability          │ Structural score < 15/100 → reject               │
└─────┴──────────────────────┴──────────────────────────────────────────────────┘
```

### CHECK 10 Detail — Leverage-Aware Confidence

```
    adjusted_floor = base_floor ÷ √leverage

    For a 55% base floor:
        1x ETF:  55% ÷ √1 = 55.0%  (standard bar)
        3x ETP:  55% ÷ √3 = 31.8%  (lower bar — leverage amplifies returns)
        5x ETP:  55% ÷ √5 = 24.6%  (lowest bar — highest amplification)
```

### VIX Hysteresis (CHECK 20)

To prevent flip-flopping between regimes at VIX boundaries:

```
    VIX = 24.5  →  Normal (below 25 entry threshold)
    VIX = 25.5  →  ENTER "VIX High" state → REDUCE regime
    VIX = 24.0  →  STAY in "VIX High" (above 22 exit threshold)
    VIX = 21.5  →  EXIT "VIX High" → back to Normal

    Deadbands:
        VIX High:    enter at 25, exit at 22  (3-point band)
        VIX Extreme: enter at 35, exit at 30  (5-point band)
```

---

## 9. EXIT STRATEGY — CHANDELIER TRAILING STOPS

### The 5-Rung Profit Ladder

The Chandelier exit uses a "rung" system that progressively tightens stops as profit grows:

```
Price
  ▲
  │
  │   ╭── Rung 5 (+4.0%) ─── Trail 0.5×ATR below peak (tight tail capture)
  │  ╭┤
  │ ╭┤│── Rung 4 (+2.5%) ─── Trail 0.75×ATR below peak (momentum ride)
  │╭┤││
  │┤│││── Rung 3 (+1.5%) ─── Trail 1.0×ATR below peak (COMPOUNDING UNIT ★)
  ├┤││││
  │││││── Rung 2 (+0.8%) ─── Stop = breakeven + fees (LOCK IN)
  ├┤││││
  ENTRY ── Rung 1 (+0.0%) ── Stop = entry - 2.0×ATR (initial risk)
  │
  ▼

  ★ The "Compounding Unit": Most trades are designed to capture Rung 3 (+1.5%)
    consistently, then compound. Getting to Rung 3 on a 3x ETP = ~4.5% actual gain.
```

### Stop Ratchet Rule

**Stops can ONLY increase, never decrease.** Once a stop ratchets up, it stays there:

```
    Tick 1: Entry at £42.50, Stop at £41.00 (Rung 1, 2×ATR below)
    Tick 2: Price rises to £42.85 (+0.8%) → Rung 2! Stop moves to £42.63 (breakeven + fees)
    Tick 3: Price dips to £42.70 → Stop STAYS at £42.63 (never decreases!)
    Tick 4: Price rises to £43.15 (+1.5%) → Rung 3! Stop = £43.15 - 1.0×ATR
    Tick 5: Price keeps rising to £44.20 → Stop trails at £44.20 - 1.0×ATR
    ...eventually price reverses, hits trailing stop → SELL
```

### The 8 Adaptive Multipliers (Infinite Chandelier)

The base trail distance is modified by 8 contextual factors:

```
    effective_trail = base_trail × (vol × corr × time × momentum
                                    × liquidity × heat × regime × mega_runner)

    ┌─────────────────┬──────────┬───────────────────────────────────────────┐
    │ Multiplier      │ Range    │ Effect                                    │
    ├─────────────────┼──────────┼───────────────────────────────────────────┤
    │ Volatility      │ 0.8–1.5  │ High vol → WIDER stops (avoid whipsaw)   │
    │ Correlation     │ 0.9–1.1  │ High correlation → TIGHTER (systemic)    │
    │ Time Decay      │ 0.8–1.0  │ Near market close → TIGHTER              │
    │ Momentum        │ 1.0–1.3  │ Strong trend → WIDER (let it run)        │
    │ Liquidity       │ 1.0–1.4  │ Illiquid → WIDER (avoid slippage)        │
    │ Portfolio Heat   │ 0.7–1.0  │ High risk exposure → TIGHTER             │
    │ Regime          │ 0.6–1.0  │ REDUCE regime → TIGHTER (defensive)      │
    │ Mega-Runner     │ 1.0–2.0  │ Profit > 3×ATR → WIDER (let it fly!)    │
    └─────────────────┴──────────┴───────────────────────────────────────────┘

    Example:
        Base trail: 0.5×ATR (Rung 5)
        Vol: 1.2 (high vol) × Corr: 0.95 × Time: 0.9 (near close)
        × Mom: 1.15 (trending) × Liq: 1.0 × Heat: 0.85 × Regime: 1.0
        × Mega: 1.3 (in profit 4×ATR)

        Combined multiplier = 1.2 × 0.95 × 0.9 × 1.15 × 1.0 × 0.85 × 1.0 × 1.3
                            = 1.24

        Effective trail = 0.5 × ATR × 1.24 = 0.62 × ATR
```

### Exit Priority Cascade

When multiple exit conditions trigger on the same tick:

```
    Priority 7 (highest): HALT/FLATTEN → emergency MarketToLimit + IOC
    Priority 6:           Hard stop-loss → LimitAtStop (or MarketSell if gapped)
    Priority 5:           Chandelier trail → LimitAtStop
    Priority 4:           EOD flatten (16:25 London) → MarketSell
    Priority 3:           Time-stop (>45min without Rung 2) → MarketSell
    Priority 2:           Dust guard (remainder < £500) → MarketSell
    Priority 1 (lowest):  Signal reversal → MarketSell
```

### Gap Protection

If price gaps through the stop (opens below the stop level):

```
    Last close: £43.00,  Stop: £42.50
    Today open: £41.80   ← GAPPED THROUGH STOP

    Normal stop order would miss. AEGIS detects this:
    IF current_price < stop_price:
        → Fire emergency MarketSell immediately
        → WAL log: "HardStopLoss (gap through)"
```

---

## 10. THE 13 TRADING STRATEGIES

The system has 13 defined strategies across 3 categories. Current policy: **max 2 active** at a time (at £10K, concentration > diversification).

### LIVE Strategies (Running in Production)

#### S1: Microstructure Momentum
```
    Type: Order Flow / Momentum
    Basis: Easley-LdP-O'Hara (2012), Chordia-Roll-Subrahmanyam (2002)

    6 factors, needs 4+ aligned:
    ┌──────────────────────────────────────────────────────────────┐
    │  1. TMR (Trade-to-Mid Ratio) > 0.25                        │
    │  2. VPIN (informed trading probability) > 0.55              │
    │  3. Spread compression < 75% of rolling average             │
    │  4. Tick momentum (Lee-Ready up/down ratio > 0.58)          │
    │  5. VWAP slope > 0.0005 (positive trend)                    │
    │  6. Amihud illiquidity < 0.01 + RVOL > 1.0                 │
    └──────────────────────────────────────────────────────────────┘
    Gate: 4+/6 signals + ADX > 15 + NOT mean-reverting regime
    Confidence: 52-77% (graduated with signal count, ADX, RVOL)
    Status: LIVE
```

#### S2: Statistical Reversion
```
    Type: Mean Reversion
    Basis: Connors & Alvarez (2008), Bollinger (2001), Jegadeesh (1990)

    5 factors, scoring system (needs score ≥ 4):
    ┌──────────────────────────────────────────────────────────────┐
    │  Bollinger z-score < -1.5/-2.0/-2.5       → 1-3 points     │
    │  RSI(2) < 15 or < 5                       → 1-2 points     │
    │  IBS < 0.25 or < 0.10                     → 1-2 points     │
    │  Volume capitulation (RVOL>2 + down bars)  → 2 points       │
    │  Mean-reversion speed > 0.5                → 1 point        │
    └──────────────────────────────────────────────────────────────┘
    Gate: Score ≥ 4 + regime != trending
    Confidence: 48-88% (48 + 4×score, +5 if MR regime confirmed)
    Status: LIVE
```

#### S3: Macro Trend Following
```
    Type: Momentum / Trend Following
    Basis: Moskowitz-Ooi-Pedersen (2012), Faber (2007)

    5 factors, needs 4+:
    ┌──────────────────────────────────────────────────────────────┐
    │  1. Dual MA crossover (SMA5 > SMA20 + close > SMA5)        │
    │  2. 12-bar momentum > 0.5%                                  │
    │  3. ADX > 20                                                │
    │  4. Volume trend slope > 0                                  │
    │  5. Hurst > 0.55 OR regime == trending                      │
    └──────────────────────────────────────────────────────────────┘
    Confidence: 50-90% (bonuses for ADX, momentum strength, Hurst)
    Status: LIVE
```

#### S4: Volatility Premium
```
    Type: Volatility strategy (long inverse OR long regular ETPs)
    Two modes:
      Low VIX (< 18):  Long inverse ETPs → base 57%, max 78%
      High VIX (> 30):  Long regular 3x ETPs → base 55%, max 75%
    Habitat: 3x ETPs only
    Status: LIVE
```

#### S5: Overnight Carry
```
    Type: Overnight drift premium
    Basis: Cliff, Cooper, Gulen (2008)
    Entry: 30 min before close + 2/3 recent bars up + NOT Friday
    Confidence: 56-78% (day-adjusted: Mon-Wed +2pp, Thu -2pp, Fri blocked)
    Status: LIVE
```

#### S7: Tail Hedge
```
    Type: Crisis hedge (long inverse ETPs during market crashes)
    Entry: VIX > 25 + IS inverse symbol + trending regime + 3/5 bars up
    Confidence: 60-88% (VIX > 35: +10, VIX > 45: +5, RVOL > 3.0: +5)
    Status: LIVE
```

### Rust-Level Execution Strategies

#### VanguardSniper (Momentum Multi-Factor)
```
    The ONLY proven live strategy — 33 trades, 52.4% WR, 1.96 Profit Factor
    Scoring: ADX momentum (15-40pts) + EMA20 trend (30pts) + Volume breakout (20-30pts)
    Volatility scaling: Moreira-Muir (2017) — applied to SIZE, not confidence
    Kelly: min(confidence/1000 × mm_scale, 0.05) = max 5%
    Auction gate: Blocks LSE open (07:50-08:00) and close (16:30-16:35)
    Status: LIVE — Primary signal producer
```

#### ApexScout (RVOL Anomaly Scanner)
```
    Type: Relative volume anomaly detection
    Scoring: RVOL excess × 50 (max 50) + bar return × 1000 (max 50)
    Habitat: ~700 tickers on 60-second OHLCV snapshots (Tier 3)
    Kelly: min(confidence/1000, 0.20)
    Status: LIVE (0 production trades yet)
```

### SHADOW Strategies (Defined, Not Yet Active)

| ID | Strategy | Confidence | Session | Status |
|----|----------|-----------|---------|--------|
| S17 | VWAP Dip Buy | 70% | LSE midday, US overlap | Shadow (0 trades) |
| S18 | Gap Fade | 72% | LSE open 08:15–10:00 | Shadow (0 trades) |
| S19 | RSI(2)/IBS MR | 75% | US close 20:30–21:00 | Shadow (low priority) |
| S20 | Cross-Market Momentum | 65% | US overlap 14:45–16:00 | Shadow (0 trades) |
| S21 | Intraday Momentum | 60% | US power hour | Disabled |

### DEAD Strategy

```
    S6: Catalyst Rotation — AUTO-KILLED
    Reason: 730-day backtest → 13% WR, PF 0.01, 554K trades
    Status: Permanently blocked in code: _auto_killed_strategies = {"S6_Catalyst"}
```

### Strategy Status Summary

```
    LIVE (generating signals):     S1, S2, S3, S4, S5, S7, VanguardSniper, ApexScout
    SHADOW (defined, not active):  S17, S18, S19, S20
    DISABLED:                      S21
    DEAD (auto-killed):            S6

    Current policy: max_active_strategies = 2
    At £10K: "Concentration is your friend. Run 2 strategies only."
```

### Tier-Based Routing

Tickers are classified into tiers based on historical alpha:

```
    Tier 1 (Vanguard): Best performers → continuous tick monitoring, all strategies
    Tier 2 (Warm):     Decent performers → continuous monitoring, reduced allocation
    Tier 3 (Apex):     Cold tickers → 60-second snapshots only (ApexScout)
    Tier 4 (Locked):   IC ≤ 0 → no trading, data collection only
```

---

## 11. INSTRUMENT UNIVERSE — WHAT IT ACTUALLY TRADES

This is NOT just a leveraged ETP system. It's a **global multi-asset engine** with autonomous discovery.

### Universe Size

```
    Live Contracts:     ~4,600 across 14 exchanges
    Discovery Universe: 36,000+ tickers (research/backtesting)
    Core 12 ETPs:       Always-on, highest priority
```

### Exchange Breakdown

| Exchange | Contracts | Instruments |
|----------|-----------|-------------|
| **SMART (US routes)** | ~3,569 | US equities (AAPL, MSFT, NVDA, TSLA...) |
| **LSE** | ~428 | UK equities (FTSE 100/250) |
| **LSEETF** | ~110 | **Leveraged/Inverse ETPs** (the Core 12 + 100 more) |
| **HKEX** | ~121 | Hong Kong (Hang Seng constituents) |
| **TSE** | ~87 | Japan (Nikkei 225 constituents) |
| **ASX** | ~146 | Australia (ASX 200 constituents) |
| **KRX** | ~70 | Korea (KOSPI constituents) |
| **EURONEXT** | ~44 | France/Netherlands/EU |
| **XETRA** | ~41 | Germany (DAX) |
| **SGX** | ~11 | Singapore |

### The Core 12 (Always-On LSE Leveraged ETPs)

```
    QQQ3.L  — 3x Nasdaq 100         QQQS.L  — 3x Short Nasdaq
    3LUS.L  — 3x US Broad (GBP)     3USS.L  — 3x US Broad (USD)
    QQQ5.L  — 5x Nasdaq 100         3SEM.L  — 3x Semiconductors
    NVD3.L  — 3x NVIDIA             TSL3.L  — 3x Tesla
    TSM3.L  — 3x TSM                MU2.L   — 2x Micron
    5SPY.L  — 5x S&P 500 (GBP)     3LTS.L  — 3x FTSE 100
```

### 3-Tier Priority System

```
    Tier 1 (Highest): Core 12 ETPs + top single-stock leveraged products
                      Always subscribed when LSE is open

    Tier 2 (Active Rotation): 50-100 mid/large cap equities
                              US mega-caps, FTSE 250, DAX 40
                              Rotated by Gemini universe curation

    Tier 3 (Research): Full 36K+ universe for alpha discovery,
                       backtesting, correlation studies
```

### Autonomous Contract Discovery

The `contract_expander.py` runs every 15 minutes, discovers high-scoring tickers from the 36K+ universe, and auto-registers them in `contracts.toml` (capped at 100/run). IBKR paper account limit: 100 concurrent market data subscriptions, rotated by session.

### NOT Traded (ISA Restrictions)

```
    ❌ Options, Futures, CFDs, Spread Bets
    ❌ Direct cryptocurrency
    ❌ Forex pairs directly
    ❌ Bonds, fixed income
    ❌ Direct China (XSHG/XSHE), India (XBOM/XNSE), Taiwan (TWSE)
    ✅ Crypto ETPs (OBTC, BTCS on LSE) — ISA-legal
    ✅ Currency ETPs (LGB3.L) — ISA-legal
    ✅ Taiwan via proxy (TSM3.L on LSE) — ISA-legal
```

---

## 12. POSITION SIZING — KELLY CRITERION PIPELINE

### The Full Sizing Pipeline

```
  Step 1: Raw Kelly
  ────────────────
      f_raw = (p × b - (1-p)) / b
      Where p = Bayesian win rate, b = avg_win/avg_loss

  Step 2: Half-Kelly Safety Cap
  ─────────────────────────────
      f_half = f_raw × 0.50

  Step 3: Hard Clamp
  ──────────────────
      f_clamped = clamp(f_half, [0.02, 0.05])
      Min 2%, Max 5% of equity per trade

  Step 4: EWA Blend with Prior
  ────────────────────────────
      f_final = 0.30 × f_clamped + 0.70 × f_prior
      (Slow adaptation: 30% new evidence, 70% historical)

  Step 5: Confidence Adjustment
  ─────────────────────────────
      shares_kelly = (f_final × equity) / price
      shares_adjusted = shares_kelly × (confidence / 100)

  Step 6: Multi-Factor Scaling
  ────────────────────────────
      shares_final = shares_adjusted × regime_scale × exchange_weight × hour_weight

  Step 7: Minimum Notional Check
  ──────────────────────────────
      IF (shares_final × price) < £1,500  →  REJECT (too small for LSE)
```

### Tier-Based Position Limits

```
    Tier 1: Max 6% of equity per position
    Tier 2: Max 4% of equity per position
    Tier 3: Max 3% of equity per position
    Tier 4: 0% (no trading)

    At £10,000 equity:
        Tier 1 max notional = £600
        Tier 2 max notional = £400
        Tier 3 max notional = £300
```

### Tier-Based Stop Widths

```
    Tier 1: 1.5×ATR (widest — gives more room for leveraged ETPs)
    Tier 2: 1.2×ATR (moderate)
    Tier 3: 1.0×ATR (tightest — aggressive exit on cold tickers)

    Example at ATR = £0.80:
        Tier 1 stop: Entry - £1.20
        Tier 2 stop: Entry - £0.96
        Tier 3 stop: Entry - £0.80
```

---

## 13. THE SELF-IMPROVING LOOP — OUROBOROS PIPELINE

Ouroboros (🐍 "snake eating its tail") runs nightly AFTER markets close. It reads the day's WAL journal and recalibrates every parameter.

### The 11-Step Pipeline

```
┌────────────────────────────────────────────────────────────────────────┐
│                    OUROBOROS NIGHTLY PIPELINE                          │
│                 (runs outside LSE hours only)                          │
│                                                                        │
│  ┌─── Step 0: GARCH Calibration ──────────────────────────────────┐  │
│  │  Fit GARCH(1,1) to 60-day history → (ω, α, β) parameters      │  │
│  │  Output: garch_params.json → Rust's real-time inference        │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 1-2: Safety & Ingest ──────────────────────────────────┐  │
│  │  1. Refuse to run if LSE is open (timing guard)                │  │
│  │  2. Cold start check (days 1-3: write conservative defaults)   │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 3: Ingest WAL ────────────────────────────────────────┐  │
│  │  Read today's WAL events (READ-ONLY — never modifies)          │  │
│  │  Extract: fills, exits, PnL, strategies, regime labels         │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 4: Bayesian Win Rate ─────────────────────────────────┐  │
│  │  WR_bayes = (wins + 1) / (total + 2)                          │  │
│  │  Output: raw_win_rate, bayesian_win_rate, trade_count          │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 5: Deflated Sharpe Ratio ─────────────────────────────┐  │
│  │  Test if Sharpe Ratio is statistically significant              │  │
│  │  Accounts for skewness, kurtosis, multiple hypothesis testing  │  │
│  │  Only computed if N ≥ 10 trades                                │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 6: Kelly Accelerator ─────────────────────────────────┐  │
│  │  Per-ticker optimal Kelly fraction:                            │  │
│  │    f* = p - (1-p)/b,  then half-Kelly, then EWA blend         │  │
│  │  Output: per-ticker Kelly updates → dynamic_weights.toml       │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 7: Exit Calibration ──────────────────────────────────┐  │
│  │  Analyze Maximum Adverse/Favorable Excursion per trade:        │  │
│  │    If Rung 5 rate > 60% → LOOSEN Chandelier (+0.2 to mult)   │  │
│  │    If early stop rate > 60% → TIGHTEN (-0.2)                  │  │
│  │  Clamp: [1.5, 4.0]                                            │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 8: Regime Hunting ────────────────────────────────────┐  │
│  │  Classify each trade into a market regime:                     │  │
│  │    bull_quiet, bull_volatile, bear_quiet, bear_volatile        │  │
│  │  Compute per-regime stats (WR, avg PnL, total PnL)            │  │
│  │  Output: best/worst regime, regime scaling multipliers         │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 9: Alpha Sieve ──────────────────────────────────────┐  │
│  │  Per-ticker Information Coefficient: IC = (2×positives/N) - 1  │  │
│  │  ASER (risk-adjusted return) = mean(pnl) / std(pnl)           │  │
│  │                                                                │  │
│  │  Tier promotion:  ASER > 0.8 → promote (Tier 3 → Tier 2)    │  │
│  │  Tier demotion:   ASER < 0.3 → demote  (Tier 1 → Tier 2)    │  │
│  │  Ticker lock:     IC ≤ 0 → LOCK (no predictive power)         │  │
│  │  Spread check:    Spread > 0.5% → demote from Vanguard        │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 10: Output Generation ────────────────────────────────┐  │
│  │  Write: dynamic_weights.toml                                   │  │
│  │         universe_classification.toml                           │  │
│  │         fx_rates.toml (refresh from yfinance)                  │  │
│  │  Archive: parameter_history/ouroboros_YYYY-MM-DD.json          │  │
│  │  Signal: SIGHUP → Rust engine hot-reloads all parameters      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌─── Step 11: Quality Gates + Escalation + Bayesian ────────────┐  │
│  │  Book 208: Check PAPER strategies → Telegram if eligible       │  │
│  │  Book 58:  Check pending escalation alerts → escalate/repeat   │  │
│  │  Book 209: Save Bayesian source calibration snapshot           │  │
│  └────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### What Ouroboros CAN and CANNOT Do

```
    CAN:                                    CANNOT:
    ✅ Adjust Kelly fractions               ❌ Submit orders
    ✅ Tune Chandelier ATR multiplier       ❌ Override risk gates
    ✅ Promote/demote tickers               ❌ Modify WAL
    ✅ Set regime scaling multipliers       ❌ Run during market hours
    ✅ Blacklist underperforming tickers    ❌ Change risk arbiter checks
    ✅ Calibrate GARCH parameters           ❌ Access live positions
    ✅ Write parameter TOML files           ❌ Touch broker state
    ✅ Validate signals (Book 207)          ❌ Bypass schema validation
    ✅ Gate PAPER strategies (Book 208)     ❌ Auto-promote without thresholds
    ✅ Bayesian source calibration (209)    ❌ Override Rust's 33 risk checks
    ✅ Escalate + flatten (Book 58)         ❌ Resume after flatten (manual)
```

---

## 14. CONFIGURATION

### Key Parameters (config.toml)

```toml
[signal]
confidence_floor = 50             # Minimum signal confidence (0-100)
outlier_win_cap_pct = 3.0         # Cap any single win at 3%

[position]
max_simultaneous_positions = 3    # Hard cap (6 for Reduce = 3/2 = 1)
portfolio_heat_limit_pct = 10.0   # Sum of position risk ÷ equity
sector_heat_cap_pct = 33.0        # Max exposure to one sector
cash_buffer_pct = 25.0            # Always keep 25% in cash
isa_annual_limit_gbp = 20000      # UK ISA annual deposit cap

[kelly]
fraction_cap = 0.5                # Half-Kelly (never bet full Kelly)
clamp_max = 0.05                  # Max 5% of equity per trade

[risk]
daily_drawdown_pct = 2.0          # >2% daily loss → FLATTEN
weekly_drawdown_pct = 7.0         # >7% weekly loss → FLATTEN
peak_drawdown_halt_pct = 15.0     # >15% from HWM → HALT
equity_floor_pct = 70.0           # <£7,000 → HALT
consecutive_loss_halt = 3         # 3 stop-losses in a row → HALT
daily_trade_limit = 3             # Max 3 round-trips per day (cost control)

[timing]
entry_cutoff_london = "15:45"     # No new entries after 3:45pm London
eod_flatten_time = "16:25"        # Force-sell everything at 4:25pm
stale_data_threshold_secs = 120   # No ticks for 2min → HALT

[chandelier]
initial_stop_atr_mult = 2.0       # Rung 1: stop = entry - 2×ATR
rung5_trail_atr = 0.5             # Rung 5: trail 0.5×ATR below peak
```

### Per-Exchange Parameters

```
    ┌───────────┬───────────┬──────────────┬──────────────┬──────────────┐
    │ Exchange  │ Min Entry │ Entry Cutoff │ Stale Secs   │ Size Weight  │
    ├───────────┼───────────┼──────────────┼──────────────┼──────────────┤
    │ LSE       │ £1,500    │ 15:45 London │ 120s         │ 0.90         │
    │ US        │ $300      │ 15:30 NY     │ 90s          │ 1.00         │
    │ TSE       │ ¥50,000   │ 14:30 Tokyo  │ 180s         │ 0.90         │
    │ HKEX      │ HK$2,000  │ 15:30 HK    │ 150s         │ 0.80         │
    │ XETRA     │ €500      │ 17:00 FFT   │ 120s         │ 0.85         │
    │ EURONEXT  │ €500      │ 17:00 Paris  │ 120s         │ 0.85         │
    │ SGX       │ S$500     │ 16:30 SG    │ 180s         │ 0.50         │
    └───────────┴───────────┴──────────────┴──────────────┴──────────────┘
```

### Trading Sessions (22-Hour Coverage)

```
    23:00 ─────── ModeA ──────── 08:00
           Asian + pre-market      │
                                   │
    08:00 ─────── ModeB ──────── 14:30
           European session        │
                                   │
    14:30 ─────── ModeB+ ─────── 16:35
           US overlap (peak)       │
                                   │
    16:35 ─────── ModeC ──────── 21:00
           US-only session         │
                                   │
    21:00 ─────── DARK ──────── 23:00
           Maintenance window
           No entries, no exits
           Parameters reload here
```

### Deployment Architecture

```
    ┌────────────────────────────────────────────────────────────────┐
    │  AWS EC2 (t3.medium, Amazon Linux 2)                         │
    │                                                              │
    │  ┌──────────┐     ┌──────────────────┐                      │
    │  │  AEGIS V2 │────▶│  IB Gateway      │                      │
    │  │  (Rust)   │◀────│  (Docker:        │                      │
    │  │           │     │   gnzsnz/ib-gw)  │                      │
    │  └──────────┘     └──────────────────┘                      │
    │       │                                                      │
    │       │ subprocess                                           │
    │       ▼                                                      │
    │  ┌──────────┐                                                │
    │  │  Python   │                                                │
    │  │  Brain    │ (bridge.py: S1-S7 strategies, VanguardSniper) │
    │  └──────────┘                                                │
    │                                                              │
    │  cron: Ouroboros nightly pipeline (04:52 UTC)                 │
    │  cron: FX rate refresh (6-hourly)                            │
    │  cron: Contract expander (15-min)                            │
    │  cron: Gemini universe scan (2-hourly)                       │
    │  cron: Gemini dark horse scan (15-min, during trading)       │
    │  cron: Claude nightly review (after Ouroboros)                │
    │  cron: Claude morning/evening briefings (07:45/21:30 UTC)    │
    │                                                              │
    │  External APIs:                                              │
    │  ├── Claude Code CLI (claude -p) → Anthropic Max ($0/call)   │
    │  └── Gemini 2.5-Pro SDK → Google AI ($0 on free tier)       │
    └────────────────────────────────────────────────────────────────┘
           ↕ Port 4003 (paper) / 4001 (live)
    ┌────────────────────────────────────────────────────────────────┐
    │  Interactive Brokers                                          │
    │  TWS Gateway Server                                           │
    └────────────────────────────────────────────────────────────────┘
```

---

## 15. KEY DESIGN PRINCIPLES

### 1. Fail-Closed Risk
Every risk gate defaults to REJECT. You must pass ALL 33 checks to get an order through. If any data is missing or stale → refuse the trade.

### 2. Rust Owns Execution
Python generates advisory signals. Claude and Gemini provide analysis. **Rust makes ALL final decisions.** Neither Python, Claude, nor Gemini can submit an order, cancel an order, or modify a position. The 33-check RiskArbiter is the sole execution gatekeeper.

### 3. WAL for Crash Recovery
Every significant event is written to a Write-Ahead Log (ndjson format) before execution. On startup, the engine replays the WAL to reconstruct positions, ensuring no trades are lost across crashes or restarts.

### 4. Autonomous AI with Hard Bounds
Claude and Gemini operate autonomously — no human approval needed. Claude's nightly gate_tuning auto-applies to dynamic_weights.toml within hard bounds (Kelly [0.10-0.35], max ±10%/cycle, 30-day drift cap 50%). Claude curator soft-gates signals in real-time. Gemini seeds strategy weights and curates the universe. Hard bounds and the 33-check Rust arbiter are the safety net, not human oversight. The system starts at 0 trades and gets smarter every night.

### 5. ISA Compliance by Construction
Short selling is blocked at CHECK 1 (compile-time invariant). The £20K annual limit is tracked in the portfolio state. Blocked exchanges are enforced by the ISA gate. These are not configurable — they're structural.

### 6. Stop Monotonicity
Trailing stops can only increase, never decrease. Once you've locked in a profit level, you cannot lose it. This is enforced in the Chandelier strategy with a simple `max()` check on every tick.

### 7. Cost Awareness
At £10K capital, every round-trip costs ~0.50% (spread + commission). Daily trade limit of 3 prevents the system from churning away its capital. CHECK 28 enforces this strictly.

### 8. Leverage Awareness
All risk parameters are scaled by √leverage. A 3x ETP with 30% volatility is treated as equivalent to a 1x ETF with 90% volatility for risk purposes.

### 9. No Silent Failures
Every rejection is logged with a specific `VetoReason` enum variant (30+ variants). The Ouroboros pipeline analyses these to understand what the system is rejecting and why, enabling parameter tuning.

### 10. Deterministic Replay
The entire tick-to-trade pipeline is deterministic. Given the same WAL events and config, the engine will produce the same state on replay. This enables backtesting, debugging, and auditing.

---

## 16. GLOSSARY

| Term | Definition |
|------|-----------|
| **ATR** | Average True Range — volatility measure based on daily high/low/close |
| **Chandelier** | Trailing stop that hangs from the highest high, like a chandelier from a ceiling |
| **CVaR** | Conditional Value-at-Risk — expected loss in the worst X% of scenarios |
| **DSR** | Deflated Sharpe Ratio — Sharpe adjusted for overfitting risk |
| **ETP** | Exchange-Traded Product — includes ETFs, ETNs, leveraged/inverse products |
| **EVT** | Extreme Value Theory — statistical framework for modelling rare events |
| **GARCH** | Generalized Autoregressive Conditional Heteroskedasticity — volatility model |
| **GPD** | Generalized Pareto Distribution — used in EVT for tail modelling |
| **HWM** | High Water Mark — highest equity value achieved (for drawdown calculation) |
| **IBS** | Internal Bar Strength — (Close - Low) / (High - Low) — mean-reversion signal |
| **IC** | Information Coefficient — correlation between predicted and actual returns |
| **ISA** | Individual Savings Account — UK tax-free investment wrapper |
| **Kelly** | Kelly Criterion — optimal betting fraction based on win rate and payoff ratio |
| **LSE** | London Stock Exchange |
| **MAB** | Multi-Armed Bandit — exploration/exploitation algorithm for selection problems |
| **MAE/MFE** | Maximum Adverse/Favorable Excursion — how far against/for a trade went |
| **Ouroboros** | The nightly self-calibration pipeline (named after the self-eating snake) |
| **RVOL** | Relative Volume — current volume ÷ 20-day average volume |
| **RSI** | Relative Strength Index — momentum oscillator (0-100, <30 oversold, >70 overbought) |
| **SIGHUP** | Unix signal used to trigger hot-reload of configuration files |
| **Vanguard/Apex** | Universe tiers: Vanguard = continuous monitoring, Apex = 60s snapshots |
| **VIX** | CBOE Volatility Index — "fear gauge" derived from S&P 500 options |
| **VWAP** | Volume-Weighted Average Price — benchmark price incorporating volume |
| **WAL** | Write-Ahead Log — crash recovery journal (ndjson format) |
| **Yang-Zhang** | Volatility estimator that handles overnight gaps (used for leveraged ETPs) |

---

*Document generated by reverse-engineering the NZT-48 AEGIS V2 codebase.*
*Rust core: ~35,000 LOC across 75+ modules. Python layer: ~80,000 LOC across 100+ modules.*
*Research corpus: 224 books of quantitative finance literature.*
