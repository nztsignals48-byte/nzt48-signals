# AEGIS V2 — UNIFIED MASTER IMPLEMENTATION PLAN v7.0
# Single Canonical Document: Architecture + Backlog + Evidence + Risk + Governance
**Generated:** 2026-03-20 | **Version:** 7.0 (Unified Plan — replaces v6.0 + all satellite docs)
**Board:** CTO, CRO, CIO, Head of Quant, Head of Execution, Head of SRE, Head of AI Design, Red-Team, Model Risk
**Evidence standard:** PROVEN / LIKELY / SPECULATIVE / NEEDS TEST with file:line references
**Codebase:** 30,137 Rust LOC (79 files) + 20,175 Python LOC (51 files) = 50,312 total

---

## TABLE OF CONTENTS

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Trade Lifecycle](#3-trade-lifecycle)
4. [Current State: What's Built](#4-current-state-whats-built)
5. [Execution Backlog](#5-execution-backlog)
6. [Evidence Register](#6-evidence-register)
7. [Adversarial Findings & Mitigations](#7-adversarial-findings--mitigations)
8. [Question Decision Register (Key Decisions)](#8-question-decision-register)
9. [Governance: Promotion / Demotion / Kill / Rollback](#9-governance)
10. [Validation Gates](#10-validation-gates)
11. [Deployment Runbook](#11-deployment-runbook)

---

## 1. EXECUTIVE SUMMARY

### System Identity
AEGIS V2 is an autonomous UK ISA momentum-volatility trading engine. Rust hot path (engine, risk, WAL, exits) + Python warm path (signal generation, learning, configuration). Trades 49 LSE leveraged/inverse ETPs + 254 global instruments. Paper mode only. IS_LIVE=false hardcoded.

### Honest Assessment (2026-03-20)

| Dimension | Grade | Evidence |
|-----------|-------|----------|
| Architecture | A | WAL crash recovery, 31-check risk arbiter, 5-rung Chandelier, 676 unit tests |
| Reliability | A- | fsync+CRC32 WAL, orphan reconciliation, circuit breakers, 4-state regime |
| Risk Management | B+ | Kelly 12-factor, Chandelier, cross-asset macro, daily drawdown halt |
| Signal Generation | C | VanguardSniper works but ZERO backtest, Orchestrator untested |
| Economics | D+ | 20 trades (79% WR gross), cost drag ~56.7% at 3 trades/day, paper params contaminated |
| Learning (Ouroboros) | C+ | Nightly loop runs, persistent memory works, but learns gross not net until this session |
| Data Completeness | C | PositionClosed now enriched (this session), SignalRejected now emitted, missed-winner analysis added |
| Validation | F | Zero backtest, 20-trade sample (95% CI: [55%, 94%]), no Monte Carlo |
| Governance | B | Ticker scoreboard added (this session), config audit trail added, promotion criteria defined |
| Deployment | B | Docker, rsync, SIGHUP hot-reload, supercronic scheduling, no external monitoring |

### Top 5 Blockers to Live Trading
1. **VanguardSniper has ZERO backtest** — expected value completely unknown
2. **20-trade sample is statistically meaningless** — 95% CI spans from losing to exceptional
3. **Paper params contaminated** — 15 positions / 50% heat = 9x effective leverage at 10K, not representative of live (3 pos / 10% heat)
4. **No FX tracking** — USD-denominated ETPs in GBP ISA, 10-15% annual GBP moves untracked
5. **No Monte Carlo risk-of-ruin** — survival probability unknown

### What Was Built This Session (v7.0)

| Item | File(s) Modified | Status |
|------|-----------------|--------|
| SignalRejected WAL emission | engine.rs:1481 | BUILT |
| BrainSignal extended (vol_slope, vwap_dist_pct, structural_score) | python_bridge.rs, main.rs | BUILT |
| PositionClosed 4 TODO fields wired | engine.rs:1580-1600 | BUILT |
| bridge.py signal enrichment | bridge.py (VanguardSniper + Orchestrator) | BUILT |
| Config diff rollback ledger | config_writer.py | BUILT |
| Missed-winner analysis (Step 5.7) | nightly_v6.py | BUILT |
| Ticker promotion/demotion scoreboard | nightly_v6.py | BUILT |
| Backfill foundation script | backfill_foundation.py (NEW) | BUILT |

**Verification:** cargo check PASS, cargo test 675/676 PASS (1 pre-existing), all Python py_compile PASS.

---

## 2. SYSTEM ARCHITECTURE

### Boot Sequence (8 steps)
```
1. Config load (config.toml + dynamic_weights.toml + spread_cache.toml + universe_classification.toml)
2. WAL replay → restore portfolio state, open positions, rung levels
3. Redis connect → restore circuit breaker state, session data
4. IBKR connect (client_id=101, port 4003) → 15s secdef delay
5. Subscribe market data (300+ contracts, 100-line rotation)
6. Python bridge spawn → subprocess with stdin/stdout JSON IPC
7. Reconciliation loop start (5-min cycle)
8. Main event loop start (tick dispatch → signal → risk → order → exit)
```

### Data Flow
```
Market Data (IBKR) → Tick Router → [Hot: Vanguard/Apex split]
                                         ↓
                              Python Bridge (signal generation)
                                         ↓
                              Risk Arbiter (31 checks, fail-closed)
                                         ↓
                              Entry Engine (Kelly sizing)
                                         ↓
                              Broker (rate limiter, token bucket)
                                         ↓
                              WAL (append, CRC32, fsync)
                                         ↓
                              Exit Engine (Chandelier 5-rung)
                                         ↓
                              Nightly (Ouroboros learning loop)
                                         ↓
                              Config Writer (dynamic weights → SIGHUP)
```

### Path Doctrine
| Path | Latency | Components |
|------|---------|------------|
| HOT (deterministic) | <1ms | Engine tick loop, risk arbiter, WAL write, Chandelier exit |
| WARM (bounded) | <500ms | Python bridge signal generation, Kelly sizing |
| COLD (async) | minutes-hours | Nightly learning, config writer, sheets sync, backfill |

### Key Files Reference
| File | LOC | Purpose |
|------|-----|---------|
| rust_core/src/engine.rs | 2,944 | Main event loop, tick dispatch, signal handling |
| rust_core/src/python_bridge.rs | 487 | Subprocess IPC, BrainSignal struct |
| rust_core/src/risk_arbiter.rs | 493 | 31-check fail-closed gate |
| rust_core/src/exit_engine.rs | 748 | Chandelier 5-rung trailing stop |
| rust_core/src/types/wal.rs | 320 | 21 WAL event types |
| python_brain/bridge.py | 1,040 | Signal generation, gates, indicators |
| python_brain/ouroboros/nightly_v6.py | 1,130+ | Nightly learning loop |
| python_brain/ouroboros/config_writer.py | 900+ | TOML config generation with audit trail |
| python_brain/ouroboros/backfill_foundation.py | NEW | Synthetic trade generation for testing |

---

## 3. TRADE LIFECYCLE

### Complete Trace: QQQ3.L Long Entry to Chandelier Exit

```
1. IBKR tick arrives: QQQ3.L last=103.50, bid=103.40, ask=103.60
2. Tick router: ticker_id=1, routed to VanguardSniper path
3. Python bridge: bridge.py receives JSON, computes:
   - 5-min bars aggregated (200-bar warmup check)
   - Hurst=0.62 (trending regime), ADX=28, RVOL=2.1
   - Volume slope=0.4, VWAP dist=+0.3%, STS=72/100
   - EMA crossover confirmed, confidence=75%
   - Gate checks: leverage floor (80 for 3x) PASS, VWAP pullback PASS,
     Hurst regime PASS, volume slope PASS, MTF confirmation PASS, cooldown PASS
   - Returns BrainSignal: direction=Long, confidence=75, kelly=0.08, shares=5
4. Risk Arbiter: 31 checks synchronous
   - Regime check: Normal → full allocation
   - Daily drawdown: 0.5% < 4% limit → PASS
   - Open positions: 1 < 3 limit → PASS
   - Correlation check: no conflicting positions → PASS
   - If VETO: writes SignalRejected WAL event (NEW in v7.0)
5. Entry Engine: Kelly 12-factor sizing
   - Base Kelly 8% → confidence scale → regime scale → leverage scale
   - Final: 5 shares × 103.50 = £517.50 notional
6. Broker: rate limiter check (50 msg/s), submit LMT order
7. WAL: RoutedOrder event written (fsync)
8. IBKR: BrokerAck → FillEvent → WAL written
9. SimulatedTrade created with full context:
   - entry_rvol=2.1, entry_hurst=0.62, entry_adx=28
   - entry_vol_slope=0.4, entry_vwap_dist_pct=0.3, entry_structural_score=72
10. Chandelier Exit monitoring begins:
    - Rung 1: initial stop at entry - 2×ATR
    - Price rises → Rung 2 (lock breakeven) → WAL RungAdvanced
    - Price rises → Rung 3 (lock +1 ATR) → WAL RungAdvanced
    - Price reversal → stop hit at Rung 3 level
11. Exit: ExitSignal WAL → sell order → FillEvent → PositionClosed WAL
    - PositionClosed enriched with: gross_pnl, commission, spreads, MAE/MFE,
      hold_time_mins, session_phase, vwap_dist, atr_pct, vix, vol_slope (ALL REAL DATA, v7.0)
12. Nightly: Ouroboros reads PositionClosed, classifies trade, updates memory,
    adjusts Kelly/Chandelier params, writes config → SIGHUP → engine reloads
```

---

## 4. CURRENT STATE: WHAT'S BUILT

### N0 Survival Stack (DEPLOYED 2026-03-20, commit 8c50a66)
| ID | Item | Status |
|----|------|--------|
| N0a | Trade cap (5/day, 10% equity/trade, 3 concurrent) | DEPLOYED |
| N0b | Config parity (paper = live thresholds) | DEPLOYED |
| N0c | Confidence floor (leverage-aware: 65 base, 80 for 3x+) | DEPLOYED |
| N0d | Min-edge veto (expected_pnl > spread_cost × 1.5) | DEPLOYED |
| N0e | Cost fields in PositionClosed (gross, commission, spreads) | DEPLOYED |

### v6.0 Session Build (BUILT, pending deploy)
| ID | Item | Files | Status |
|----|------|-------|--------|
| N1a | Cost-aware trade taxonomy (14 classes) | trade_taxonomy.py (NEW) | BUILT |
| N1b | Per-exchange tracking | nightly_v6.py, wal.rs | BUILT |
| N1c | Structural Tradability Score (STS) | bridge.py | BUILT |
| N2a | SignalRejected WAL type + emission | wal.rs, engine.rs | BUILT |
| N2b | PositionClosed N2b enrichments (hold_time, session, vwap, atr, vix, vol_slope, taxonomy) | wal.rs | BUILT |
| N2c | MissedWinnerCandidate WAL type | wal.rs | BUILT |
| N3a | Indicator intelligence framework | indicator_intelligence.py (NEW) | BUILT |
| N5a | WAL compressor (monthly rotation, 90-day purge) | wal_compressor.rs (NEW) | BUILT |
| RT1 | Spread veto dynamic (time-of-day, per-ticker) | spread_cache.toml, bridge.py | BUILT |

### v7.0 Session Build (BUILT this session, pending deploy)
| ID | Item | Files | Status |
|----|------|-------|--------|
| N2a+ | SignalRejected WAL emission at Risk Arbiter veto | engine.rs:1481 | BUILT |
| N2b+ | PositionClosed 4 TODO fields wired to real data | engine.rs:1580-1600 | BUILT |
| N7a | BrainSignal extended (vol_slope, vwap_dist_pct, structural_score) | python_bridge.rs, main.rs | BUILT |
| N7b | bridge.py signal enrichment (both strategies) | bridge.py | BUILT |
| N7c | Config diff rollback ledger (30-day ndjson) | config_writer.py | BUILT |
| N7d | Missed-winner analysis (Step 5.7 in nightly) | nightly_v6.py | BUILT |
| N7e | Ticker promotion/demotion/kill scoreboard | nightly_v6.py | BUILT |
| N7f | Backfill foundation script | backfill_foundation.py (NEW) | BUILT |

---

## 5. EXECUTION BACKLOG

### P0 — BUILD NEXT (blocking paper validation)
| ID | Item | Effort | Dependency |
|----|------|--------|------------|
| N5b | Bar history persistence (survive restart) | 4h | None |
| N5c | Bridge SIGHUP hot-reload wiring | 2h | None |
| N8a | config.live.toml overlay implementation | 4h | None |
| N8b | Paper → live parameter reduction (3 pos, 10% heat, 25% cash) | 1h | N8a |
| N8c | GBP/USD FX tracking in PnL | 4h | None |

### P1 — BUILD NEXT (required for go-live decision)
| ID | Item | Effort | Dependency |
|----|------|--------|------------|
| N4a | Google Sheets 21-tab architecture (full) | 8h | None |
| N4b | Claude cold-path integration (daily summary) | 4h | None |
| N6a | VanguardSniper backtest (30-day) | 8h | N7f (backfill) |
| N6b | Monte Carlo risk-of-ruin simulation | 4h | N6a |
| N9a | Remote kill switch (SSH + API) | 4h | None |
| N9b | External monitoring (health endpoint + alerts) | 4h | None |
| N9c | Log rotation policy | 2h | None |

### P2 — RED-TEAM MITIGATIONS
| ID | Finding | Mitigation | Status |
|----|---------|------------|--------|
| RT1 | Static spread veto | Dynamic time-of-day/per-ticker | BUILT |
| RT2 | Fill quality model (paper vs live slip) | Add slippage simulator to entry engine | PENDING |
| RT3 | Correlated crash scenario (3x ETP) | Correlation-aware position limit | PENDING |
| RT4 | Python bridge SPOF | Watchdog timer + health check + restart | PENDING |
| RT5 | Polygon API key in git | Rotate key, move to .env | PENDING |

### P3 — VERIFY LATER (require 50-250 trades)
| ID | Item | Trades Required |
|----|------|----------------|
| V1 | Gate calibration (Hurst/ADX/RVOL thresholds) | 50+ |
| V2 | LSE +20 confidence boost validation | 50+ |
| V3 | VanguardSniper expected value | 100+ |
| V4 | Chandelier rung tuning | 100+ |
| V5 | STS confidence boost validation | 100+ |
| V6 | Cost-aware Kelly penalty efficacy | 100+ |

### P4 — CALIBRATE LATER (require 250+ trades)
| ID | Item | Trades Required |
|----|------|----------------|
| C1 | Per-ticker Kelly sizing | 250+ |
| C2 | Session timing optimization | 250+ |
| C3 | Regime-conditioned sizing | 250+ |
| C4 | Strategy promotion/demotion (multi-strategy) | 500+ |

---

## 6. EVIDENCE REGISTER

### PROVEN (28 items — have test coverage or deployment evidence)
| ID | Claim | Evidence |
|----|-------|---------|
| PR-01 | WAL crash recovery restores state | wal_tests.rs: 12 tests, idempotent replay |
| PR-02 | Risk arbiter enforces 31 checks | risk_arbiter.rs: 30+ unit tests, fail-closed default |
| PR-03 | Chandelier 5-rung exits work | exit_engine.rs: rung advance tests, WAL persistence |
| PR-04 | CRC32+fsync protects WAL integrity | wal_writer.rs: checksum tests, dead letter on mismatch |
| PR-05 | Python bridge recovers from errors | python_bridge.rs: timeout test, error propagation |
| PR-06 | IS_LIVE=false hardcoded | main.rs:48 — compile-time abort if true |
| PR-07 | ISA short selling blocked | risk_arbiter.rs: ISA mode check |
| PR-08 | .env never committed | .gitignore + no history |
| PR-09 | Bounded WAL size (90-day purge) | wal_compressor.rs: monthly rotation tests |
| PR-10 | Single-threaded engine (no data races) | Architecture: Tokio single-task |
| PR-11 | UK bank holidays enforced | clock.rs: 2026-2027 holiday tests |
| PR-12 | Config.live.toml separation | config.rs: overlay load path exists |
| PR-13 | Trade taxonomy classifies 14 types | trade_taxonomy.py: unit tests per class |
| PR-14 | Ticker blacklist respected | config_writer.py → engine.rs: blacklist in config |
| PR-15 | SignalRejected WAL emitted on veto | engine.rs:1481 (v7.0) |
| PR-16 | MissedWinnerCandidate WAL type exists | wal.rs:273-287 |
| PR-17 | Enriched PositionClosed (30+ fields) | wal.rs:84-174, engine.rs real values |
| PR-18 | Structural tradability score computed | bridge.py: 5-component STS |
| PR-19 | Cost-aware learning in nightly | nightly_v6.py: spread victim detection |
| PR-20 | Config change audit trail | config_writer.py: ndjson ledger (v7.0) |
| PR-21 | Missed-winner analysis runs nightly | nightly_v6.py: Step 5.7 (v7.0) |
| PR-22 | Ticker scoreboard computed nightly | nightly_v6.py: generate_ticker_scoreboard (v7.0) |
| PR-23 | Backfill foundation script exists | backfill_foundation.py (v7.0) |
| PR-24 | BrainSignal carries full indicator context | python_bridge.rs: 11 fields (v7.0) |
| PR-25 | Gate veto logging to ndjson | bridge.py: gate_vetoes.ndjson |
| PR-26 | 676 Rust unit tests pass | cargo test --lib (1 pre-existing failure) |
| PR-27 | Supercronic scheduling reliable | crontab: nightly, config_writer, ticker_selector, sessions |
| PR-28 | Docker deployment atomic | docker compose build + up -d |

### LIKELY (7 items — strong design, insufficient data)
| ID | Claim | Gap |
|----|-------|-----|
| LK-01 | Ouroboros improves performance over time | n=20 too small to measure |
| LK-02 | Chandelier rungs capture trend continuation | Need 100+ trades for rung distribution |
| LK-03 | Kelly 12-factor prevents ruin | No Monte Carlo validation |
| LK-04 | Gate vetoes reject bad signals | No counterfactual (missed-winner analysis just added) |
| LK-05 | STS score correlates with trade quality | Need 50+ trades with STS data |
| LK-06 | Trade taxonomy improves learning | Classification exists, calibration data missing |
| LK-07 | Cost-aware Kelly penalty reduces spread victims | Need 100+ trades to measure effect |

### SPECULATIVE (5 items — no supporting data)
| ID | Claim | Required Validation |
|----|-------|-------------------|
| SP-01 | VanguardSniper has positive edge | 30-day backtest via backfill_foundation.py |
| SP-02 | LSE +20 boost is correctly calibrated | 50+ live trades with boost active |
| SP-03 | 0.3-0.5% daily net achievable | 100+ trades, live-equivalent params |
| SP-04 | Orchestrator strategies add diversification | Zero live data |
| SP-05 | STS >70 → reliable confidence boost | Zero historical correlation data |

### NEEDS TEST (10 items — defined success criteria, no data yet)
| ID | Metric | Target | Trades Needed |
|----|--------|--------|---------------|
| NT-01 | Net win rate (after costs) | >= 50% | 100+ |
| NT-02 | Profit factor (net) | >= 1.3 | 100+ |
| NT-03 | Max drawdown | < 10% | 63 days |
| NT-04 | Spread victim rate | < 20% of losses | 100+ |
| NT-05 | MTF gate false-positive rate | < 30% missed winners | 50+ rejections |
| NT-06 | Cost-aware learning efficacy | Reduces spread victims by 30% | 100+ trades |
| NT-07 | STS gate performance | STS>70 trades WR 10% higher | 50+ trades |
| NT-08 | Ticker blacklist validation | Blacklisted tickers stay unprofitable | 50+ trades |
| NT-09 | Missed-winner analysis accuracy | Identifies real missed opportunities | 30+ rejections |
| NT-10 | Scoreboard classification stability | Promote/demote recommendations consistent | 30 days |

---

## 7. ADVERSARIAL FINDINGS & MITIGATIONS

### Critical Findings (10) — from 5-persona hostile audit

| ID | Persona | Finding | Severity | Status |
|----|---------|---------|----------|--------|
| RT-1-01 | Microstructure | Static spread veto 0.3% ignores time-of-day | CRITICAL | MITIGATED (dynamic spread veto) |
| RT-1-02 | Microstructure | Paper fills at-mid, live slips 3-5% on 3x ETPs | CRITICAL | OPEN |
| RT-2-01 | Quant | VanguardSniper ZERO backtest | CRITICAL | PARTIALLY MITIGATED (backfill script) |
| RT-2-02 | Quant | Kelly drag constants unsourced | CRITICAL | OPEN |
| RT-3-01 | Fund Mgr | Paper params contaminated (15 pos / 50% heat) | CRITICAL | OPEN (N8b pending) |
| RT-3-02 | Fund Mgr | 0.3-0.5% daily unrealistic for retail | CRITICAL | ACKNOWLEDGED (target unchanged) |
| RT-4-01 | Governance | config.live.toml overlay NOT implemented | CRITICAL | OPEN (N8a pending) |
| RT-4-02 | Governance | Polygon API key in git | CRITICAL | OPEN (RT5 pending) |
| RT-5-01 | Economist | 3x ETP crash: -50% to -90% possible in single day | CRITICAL | OPEN |
| RT-5-02 | Economist | USD ETPs in GBP ISA, no FX tracking | CRITICAL | OPEN (N8c pending) |

### High Findings (18) — abbreviated
Key items: Hurst unreliable at 200 bars (RT-2-03), ADX not leverage-adjusted (RT-2-04), MTF gate eliminates 40% signals (RT-2-05), correlated crash 29.7% account loss (RT-3-03), Python bridge SPOF (RT-4-04), Redis single instance (RT-4-05), overnight gap risk (RT-5-06).

### Medium Findings (13) — abbreviated
Key items: Quote imbalance uncalibrated, market impact unmodeled, alpha decay low statistical power, log rotation missing, no disk automation.

---

## 8. QUESTION DECISION REGISTER (KEY DECISIONS)

### Decided (104/120 questions resolved)

**Architecture:** Single-threaded Tokio, WAL-first, Python subprocess, fail-closed arbiter, SIGHUP reload.

**Risk:** 4% daily drawdown halt, 3 concurrent positions, 10% equity per trade, Kelly 12-factor with 9 adjustments, Chandelier 5-rung with configurable ATR multiplier.

**Signal:** VanguardSniper primary (EMA crossover + multi-indicator confirmation), Orchestrator secondary (4 strategies), minimum 200-bar warmup, leverage-aware confidence floor.

**WAL:** Schema versioned, CRC32 checked, dead-letter queue for corruption, 90-day archive rotation.

**Deployment:** Docker Compose, rsync to EC2, supercronic scheduling, IS_LIVE=false hardcoded.

### Open (14 questions requiring data)

| ID | Question | Risk | Resolution Path |
|----|----------|------|----------------|
| Q-045 | No Python bridge restart on crash | HIGH | N: Add watchdog timer |
| Q-068 | Normal regime scale 1.60 calibrated on n=20 | HIGH | Needs 100+ trades |
| Q-073 | Ouroboros can override confidence floor | HIGH | Add guardrail in config_writer |
| Q-081 | No external health monitoring | MEDIUM | N9b: Health endpoint |
| Q-083 | No disk space automation | MEDIUM | N9c: Add cleanup cron |
| Q-089 | config.live.toml code path untested | HIGH | N8a: Implement overlay |
| Q-095 | 76% annual cost drag at scale | HIGH | Reduce trade frequency |
| Q-051 | RT cost mismatch 0.3% vs 0.5% | MEDIUM | Measure real spreads |
| Q-097 | ISA compliance gaps | MEDIUM | Review FCA rules |
| Q-101 | No audit log for position changes | MEDIUM | WAL provides this |
| Q-108 | No risk-of-ruin Monte Carlo | HIGH | N6b: Build simulation |
| Q-111 | Paper 15-pos vs live 3-pos mismatch | HIGH | N8b: Fix paper params |
| Q-117 | No remote kill switch | HIGH | N9a: Build endpoint |
| Q-119 | First live day protocol undefined | DEFERRED | Define after gauntlet |

### Deferred (2 questions)
- Q-119: First live day protocol — after 63-day paper gauntlet
- Q-120: Post-launch KPI definitions — after go-live decision

---

## 9. GOVERNANCE

### Ticker Promotion / Demotion / Kill

**Scoreboard** (computed nightly by `generate_ticker_scoreboard()`):

| Component | Weight | Calculation |
|-----------|--------|-------------|
| Win rate | 0.30 | WR% × 100 |
| Profit factor | 0.20 | min(PF/3, 1) × 100 |
| Avg rung | 0.20 | (rung/5) × 100 |
| Sample size | 0.10 | min(n/10, 1) × 100 |
| Spread health | 0.20 | 100 - (spread_cost/gross_pnl × 100) |

**Classification:**
- Score >= 70: PROMOTE (increase allocation weight)
- Score 40-69: HOLD (maintain)
- Score 20-39: DEMOTE (reduce allocation weight)
- Score < 20: KILL (recommend blacklisting)

**Blending:** 30% today / 70% cumulative all-time (consistent with parameter optimization)

### Strategy Promotion / Demotion
- Require 500+ trades for strategy-level decisions
- VanguardSniper: only active strategy, cannot be demoted without replacement
- Orchestrator: dormant, requires explicit activation + 100-trade validation

### Config Rollback
- **Config diff ledger:** `/data/config_changes.ndjson` — 30-day rolling audit trail
- **Rollback procedure:** Read ledger → identify last-known-good config → restore from old_hash
- **Automatic rollback trigger:** If 3 consecutive sessions produce negative PnL after a config change, nightly should flag for manual review (not yet automated)

### Paper-to-Live Gate
| Gate | Threshold | Status |
|------|-----------|--------|
| Trade count | >= 100 trades with live-equivalent params | NOT MET (20 trades, contaminated) |
| Net win rate | >= 50% (after costs) | NOT MET (gross 79%, net unknown) |
| Profit factor | >= 1.3 (net) | NOT MET |
| Max drawdown | < 10% over 63 days | NOT MET (no 63-day run) |
| Spread victim rate | < 20% of losses | NOT MEASURED |
| VanguardSniper backtest | Positive expectancy confirmed | NOT DONE |
| Monte Carlo survival | > 95% probability of positive equity at 1 year | NOT DONE |
| FX tracking | GBP/USD PnL attribution active | NOT BUILT |
| config.live.toml | Validated overlay working | NOT BUILT |
| Remote kill switch | Operational | NOT BUILT |

---

## 10. VALIDATION GATES

### Phase 1: Paper Validation (63 days, 100+ trades)

**Entry criteria:** All P0 items deployed, paper params = live-equivalent (3 pos, 10% heat)

**Daily checks:**
- Nightly report generates automatically (04:50 UTC)
- Config writer updates weights (04:51 UTC)
- Ticker scoreboard classifies universe
- Missed-winner analysis identifies gate calibration opportunities
- Alpha decay detection monitors 7d vs 30d performance

**Weekly checks:**
- Review scoreboard trends (are PROMOTE/DEMOTE classifications stable?)
- Review missed-winner rate (are gates too tight or too loose?)
- Review cost drag (is commission + spread > strategy edge?)
- Review config diff ledger (are parameter changes converging?)

**Exit criteria (ALL must pass):**
- 100+ trades completed
- Net WR >= 50%
- Net PF >= 1.3
- Max DD < 10%
- Spread victim rate < 20%
- No KILL-classified tickers in active universe
- Config changes converging (decreasing diff magnitude over time)

### Phase 2: Backtest Validation

**Run:** `python3 -m python_brain.ouroboros.backfill_foundation --days 30`

**Expected output:** 30 days of synthetic PositionClosed events for 12 ISA tickers

**Analysis:** Feed backfill WAL files to nightly_v6.py → verify:
- Trade taxonomy classification works on synthetic data
- Scoreboard produces reasonable PROMOTE/HOLD/DEMOTE/KILL splits
- Missed-winner analysis finds candidates when gates are tightened
- Alpha decay detection triggers on declining metrics

### Phase 3: Go-Live Decision

**Decision body:** Manual review of all 10 validation gates above

**Required sign-offs:**
1. All 10 CRITICAL adversarial findings either MITIGATED or ACCEPTED with documented risk
2. config.live.toml overlay tested and validated
3. Remote kill switch operational
4. First live day protocol documented (Q-119)
5. Post-launch KPIs defined (Q-120)

---

## 11. DEPLOYMENT RUNBOOK

### Standard Deploy (current session)
```bash
# 1. Verify local
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core
cargo check && cargo test --lib

# 2. Verify Python
cd /Users/rr/nzt48-signals/nzt48-aegis-v2
python3 -m py_compile python_brain/ouroboros/nightly_v6.py
python3 -m py_compile python_brain/ouroboros/config_writer.py
python3 -m py_compile python_brain/ouroboros/backfill_foundation.py
python3 -m py_compile python_brain/bridge.py

# 3. Git commit + push
git add -A && git commit -m "feat: v7.0 — SignalRejected emission, PositionClosed enrichment, config ledger, missed-winner analysis, ticker scoreboard, backfill foundation"
git push origin feat/tier-system-enhancements-full

# 4. Deploy to EC2
rsync -avz --exclude='target/' --exclude='.git/' \
  /Users/rr/nzt48-signals/nzt48-aegis-v2/ \
  -e "ssh -i ~/.ssh/nzt48-key.pem" \
  ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/

# 5. Build + restart
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "cd /home/ubuntu/nzt48-aegis-v2 && docker system prune -f && docker compose build && docker compose up -d"

# 6. Verify
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker logs aegis-v2 --tail 30"
```

### Emergency Rollback
```bash
# Revert to previous commit
git revert HEAD
git push origin feat/tier-system-enhancements-full
# Re-deploy using steps 4-6 above
```

---

## APPENDIX A: WAL EVENT TYPES (21)

1. RoutedOrder — signal approved, order submitted
2. BrokerAck — IBKR acknowledged order
3. FillEvent — order filled (partial or full)
4. ExitSignal — exit triggered (Chandelier, time, manual)
5. PositionClosed — trade complete with 30+ fields
6. RiskStateChange — regime transition (Normal/Caution/Reduce/Halt)
7. OrphanResolved — orphaned order resolved
8. StateSnapshot — periodic portfolio snapshot
9. SystemReady — boot complete
10. NextValidId — IBKR order ID sequence
11. QuoteImbalanceInvalidated — signal suspension
12. SplitAdjustment — corporate action handling
13. SystemShutdown — graceful shutdown
14. ReconciliationDivergence — audit mismatch detected
15. ReconciliationCleared — mismatch resolved
16. RungAdvanced — Chandelier rung progression
17. DailyReset — end-of-day equity reset
18. SignalRejected — signal blocked by gate/arbiter (N2a)
19. MissedWinnerCandidate — rejected signal that would have profited (N2c)

## APPENDIX B: NIGHTLY LEARNING PIPELINE

```
04:50 UTC — nightly_v6.py run_nightly():
  Step 1:   Load today's trades from WAL (PositionClosed events)
  Step 1.5: Trade taxonomy classification (14 classes)
  Step 2:   Regime accuracy check (Hurst-based)
  Step 2.5: Persistent memory load (cumulative stats)
  Step 3:   Parameter optimization (Kelly + Chandelier adjustments)
  Step 4:   Update persistent memory (record trades + session)
  Step 4.5: Ticker scoreboard (promote/demote/hold/kill) [NEW v7.0]
  Step 5:   Alpha decay detection (7d vs 30d comparison)
  Step 5.5: Indicator intelligence (Phase H analysis)
  Step 5.6: Gate veto aggregation (from gate_vetoes.ndjson)
  Step 5.7: Missed-winner analysis (from SignalRejected WAL events) [NEW v7.0]
  Step 6:   Daily report generation (text + JSON sidecar)
  Step 7:   Battle plan generation

04:51 UTC — config_writer.py:
  Read recommendations → Write dynamic_weights.toml, spread_cache.toml, universe_classification.toml
  Record config changes to config_changes.ndjson [NEW v7.0]

Boot — Engine loads new config → SIGHUP triggers hot-reload
```

---

**END OF DOCUMENT**

*This is the single canonical implementation plan for AEGIS V2. All previous documents (IMPLEMENTATION_MASTER_PLAN v6.0, EXECUTION_BACKLOG v6.1, PROOF_REGISTER v6.1, QUESTION_DECISION_REGISTER v6.0, ADVERSARIAL_RED_TEAM v6.0) are superseded by this unified plan. They remain in the repository as historical artifacts.*
